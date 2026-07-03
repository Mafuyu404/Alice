"""QQ autonomous participation decision logic."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Callable, Iterable

import requests

from kokoro.core import config as cfg
from kokoro.core import input_events
from kokoro.core import memory as memory_mod
from kokoro.core import prompts
from kokoro.core import token_usage
from kokoro.action.tools.qq import media as qq_media

logger = logging.getLogger(__name__)

from kokoro.action.tools.qq.environment import QQEnvironment
from kokoro.action.tools.qq.models import QQContextPacket
from kokoro.action.tools.qq.helpers import (
    _clip_log,
    _compact_character,
    _default_participation_user,
    _extract_json_object,
    _file_uri,
    _format_packet_for_decision,
    _has_direct_social_signal,
    _looks_like_misowned_task,
    _looks_like_unbacked_action_promise,
    _memory_context_for_packets,
    _most_attended_packet,
    _recent_self_context_for_packets,
    _safe_context,
    _salvage_partial_decision,
    _silence_reason_ignores_direct_signal,
    _sticker_cq,
    _sticker_query_for_packets,
    _sticker_segment,
    _with_memory_context,
    clean_qq_reply,
)


class QQParticipationDecision:
    action: str = "silence"
    conversation_id: str = ""
    message: str = ""
    reason: str = ""
    sticker_id: str = ""
    message_segments: list[dict] = field(default_factory=list)

    @property
    def payload(self):
        return self.message_segments if self.message_segments else self.message

    @classmethod
    def from_dict(cls, data: dict) -> "QQParticipationDecision":
        action = str(data.get("action", "silence") or "silence").strip().lower()
        if action not in {"silence", "say", "send_sticker", "retire_sticker"}:
            action = "silence"
        message = str(data.get("message", "") or "").strip()
        sticker_id = str(data.get("sticker_id", "") or data.get("image_id", "") or "").strip()
        if action == "say" and not message:
            action = "silence"
        return cls(
            action=action,
            conversation_id=str(data.get("conversation_id", "") or "").strip(),
            message=message,
            reason=str(data.get("reason", "") or "").strip(),
            sticker_id=sticker_id,
        )


class QQAutonomousParticipant:
    """Ask the LLM whether the character wants to participate in QQ now."""

    def __init__(
        self,
        *,
        session,
        model: str,
        cooldown_seconds: float = 45.0,
        max_message_chars: int = 260,
        environment: QQEnvironment | None = None,
    ) -> None:
        self.session = session
        self.model = model
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.max_message_chars = max(40, int(max_message_chars))
        self.environment = environment

    def decide(self, packets: Iterable[QQContextPacket]) -> QQParticipationDecision:
        packet_list = [packet for packet in packets if packet.conversation_id != "private:self"]
        if not packet_list:
            return QQParticipationDecision()
        packet_list = _with_memory_context(self.session, packet_list)

        system_prompt = "\n\n".join(
            part
            for part in (
                prompts.skill("social_presence"),
                prompts.skill("memory_cognition"),
                prompts.get("qq.participation_system", "") or prompts.get("qq.default_participation_system", ""),
            )
            if part
        )
        recent_self_context = _recent_self_context_for_packets(packet_list)
        sticker_query = _sticker_query_for_packets(
            packet_list,
            inner_stream=_safe_context(getattr(self.session, "inner_stream", None)),
            cognition_context=_safe_context(getattr(self.session, "cognition", None)),
        )
        cognition_context = _cognition_context_for_packets(self.session, packet_list)
        user_prompt = prompts.format_prompt(
            "qq.participation_user",
            name=self.session.character_name,
            user_name=self.session.user_name,
            character_profile=_compact_character(self.session.character_data),
            inner_stream=_safe_context(getattr(self.session, "inner_stream", None)) or "无",
            cognition_context=cognition_context or "无",
            memory_context=_memory_context_for_packets(packet_list) or "无",
            recent_self_context=recent_self_context or "无",
            summary=getattr(self.session, "summary", "") or "无",
            sticker_candidates=qq_media.sticker_candidates_for_context(sticker_query, limit=30),
            packets="\n\n---\n\n".join(
                _format_packet_for_decision(p)
                + f"\nrecall_anchors: {', '.join(p.recall_anchors) if p.recall_anchors else 'none'}"
                + f"\nrelated_memory:\n{p.memory_context or 'none'}"
                for p in packet_list
            ),
        ) or _default_participation_user(self.session, packet_list)

        try:
            raw = self._call_llm(system_prompt, user_prompt)
            data = _extract_json_object(raw)
            if not data:
                data = _salvage_partial_decision(raw)
            if not data:
                logger.warning("QQ participation non-JSON raw: %s", _clip_log(raw))
                if not str(raw or "").strip():
                    fallback = self._fallback_direct_reply(packet_list, system_prompt)
                    if fallback.action == "say":
                        return fallback
                repair_prompt = prompts.format_prompt(
                    "qq.participation_repair_user",
                    user_prompt=user_prompt,
                )
                raw = self._call_llm(
                    system_prompt + "\n\n" + prompts.get("qq.participation_repair_system_suffix", ""),
                    repair_prompt,
                )
                data = _extract_json_object(raw)
                if not data:
                    data = _salvage_partial_decision(raw)
            if not data:
                logger.warning("QQ participation repair non-JSON raw: %s", _clip_log(raw))
                fallback = self._fallback_direct_reply(packet_list, system_prompt)
                if fallback.action == "say":
                    return fallback
                raise ValueError("QQ participation LLM did not return JSON object")
            decision = QQParticipationDecision.from_dict(data)
        except Exception as exc:
            logger.warning("QQ participation decision failed: %s", exc)
            return QQParticipationDecision(reason=type(exc).__name__)

        valid_ids = {p.conversation_id for p in packet_list}
        valid_ids.discard("private:self")
        if decision.action in {"say", "send_sticker"} and decision.conversation_id not in valid_ids:
            return QQParticipationDecision(reason="LLM chose an unknown conversation")
        if decision.action == "retire_sticker":
            item = qq_media.retire_sticker(
                decision.sticker_id,
                reason=decision.reason or decision.message,
                actor=getattr(self.session, "character_name", ""),
            )
            if item:
                record = getattr(self.session, "record_self_action", None)
                if callable(record):
                    record(
                        f"我决定以后不再使用表情包 {decision.sticker_id}。原因：{decision.reason or decision.message}",
                        source="sticker_library",
                        action="retire_sticker",
                        metadata={"sticker_id": decision.sticker_id, "reason": decision.reason},
                    )
                return QQParticipationDecision(reason=f"retired sticker {decision.sticker_id}")
            return QQParticipationDecision(reason=f"unknown sticker_id for retire: {decision.sticker_id}")
        if decision.action == "send_sticker":
            return self.materialize_sticker_decision(decision, packet_list, system_prompt=system_prompt)
        if decision.action == "say":
            cleaned = clean_qq_reply(decision.message, self.session.character_name)
            if not cleaned:
                return QQParticipationDecision(reason="empty cleaned message")
            if _looks_like_unbacked_action_promise(cleaned, packet_list):
                return QQParticipationDecision(
                    action="silence",
                    conversation_id="",
                    message="",
                    reason="promise guard: cannot claim an unstarted external action",
                )
            if _looks_like_misowned_task(cleaned, packet_list):
                logger.info(
                    "QQ participation suppressed likely misowned task: conversation=%s message=%r",
                    decision.conversation_id,
                    cleaned,
                )
                return QQParticipationDecision(
                    action="silence",
                    conversation_id="",
                    message="",
                    reason="relation guard: likely other-member task",
                )
            if len(cleaned) > self.max_message_chars:
                cleaned = cleaned[: self.max_message_chars].rstrip()
            return QQParticipationDecision(
                action="say",
                conversation_id=decision.conversation_id,
                message=cleaned,
                reason=decision.reason,
            )
        if _has_direct_social_signal(packet_list) and _silence_reason_ignores_direct_signal(decision.reason):
            fallback = self._fallback_direct_reply(packet_list, system_prompt)
            if fallback.action == "say":
                return fallback
        return decision

    def materialize_sticker_decision(
        self,
        decision: QQParticipationDecision,
        packets: list[QQContextPacket] | None = None,
        *,
        system_prompt: str = "",
    ) -> QQParticipationDecision:
        sticker_item = qq_media.resolve_sticker(decision.sticker_id)
        if not sticker_item:
            fallback_query = " ".join([decision.sticker_id, decision.message, decision.reason]).strip()
            sticker_item = qq_media.fallback_sticker(
                fallback_query,
                min_score=0.55,
            )
            if sticker_item:
                logger.warning(
                    "QQ participation unknown sticker_id=%r, fallback sticker_id=%r",
                    decision.sticker_id,
                    sticker_item.get("id", ""),
                )
        if not sticker_item:
            if decision.message:
                return QQParticipationDecision(
                    action="say",
                    conversation_id=decision.conversation_id,
                    message=clean_qq_reply(decision.message, self.session.character_name),
                    reason="unknown sticker_id; sent companion text only",
                )
            if packets:
                fallback = self._fallback_direct_reply(packets, system_prompt)
                if fallback.action == "say":
                    return fallback
            return QQParticipationDecision(reason="unknown sticker_id")
        sticker_id = str(sticker_item.get("id") or decision.sticker_id)
        path = qq_media.resolve_sticker_path(sticker_id)
        if not path:
            return QQParticipationDecision(reason="sticker path missing")
        file_uri = _file_uri(path)
        cq = _sticker_cq(file_uri)
        cleaned_message = clean_qq_reply(decision.message, self.session.character_name) if decision.message else ""
        message = (cleaned_message + " " + cq).strip() if cleaned_message else cq
        segments: list[dict] = []
        if cleaned_message:
            segments.append({"type": "text", "data": {"text": cleaned_message}})
        segments.append(_sticker_segment(file_uri))
        return QQParticipationDecision(
            action="say",
            conversation_id=decision.conversation_id,
            message=message,
            reason=decision.reason,
            sticker_id=sticker_id,
            message_segments=segments,
        )

    def _fallback_direct_reply(
        self,
        packets: list[QQContextPacket],
        system_prompt: str,
    ) -> QQParticipationDecision:
        target = _most_attended_packet(packets)
        if target is None or not target.attention_lines:
            return QQParticipationDecision(reason="non-json fallback found no direct social signal")
        try:
            prompt = prompts.format_prompt(
                "qq.direct_fallback_user",
                name=self.session.character_name,
                label=target.label,
                attention=target.attention_summary,
                lines="\n".join(target.lines[-16:]),
            )
            raw = self._call_llm(
                system_prompt + "\n\n" + prompts.get("qq.direct_fallback_system_suffix", ""),
                prompt,
                json_mode=False,
            )
            message = clean_qq_reply(raw, self.session.character_name)
            if not message or message.upper() == "SILENCE" or message == "沉默":
                return QQParticipationDecision(reason="direct fallback chose silence")
            if len(message) > self.max_message_chars:
                message = message[: self.max_message_chars].rstrip()
            return QQParticipationDecision(
                action="say",
                conversation_id=target.conversation_id,
                message=message,
                reason="json fallback direct reply to social signal",
            )
        except Exception as exc:
            logger.warning("QQ direct fallback failed: %s", exc)
            return QQParticipationDecision(reason=type(exc).__name__)

    def _call_llm(self, system_prompt: str, user_prompt: str, *, json_mode: bool = True) -> str:
        from kokoro.core import deepseek_api

        model = self.model
        openai_compatible = cfg.is_deepseek_model(model)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        if openai_compatible:
            return deepseek_api.chat(
                messages,
                model=model,
                temperature=0.2,
                max_tokens=512,
                json_mode=json_mode,
                function="qq_participation",
            )["content"]

        resp = requests.post(
            f"{cfg.llm_url().rstrip('/')}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 512},
            },
            timeout=45,
        )
        resp.raise_for_status()
        data = resp.json()
        pt = int(data.get("prompt_eval_count", 0))
        ct = int(data.get("eval_count", 0))
        if pt or ct:
            token_usage.record(model, "qq_participation", pt, ct)
        return data.get("message", {}).get("content", "").strip()
