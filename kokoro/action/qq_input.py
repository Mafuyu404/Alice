"""QQ environment input and autonomous participation helpers.

This module keeps QQ-specific runtime logic thin: it buffers raw chat messages,
formats recent group/private context as natural-language environment packets,
and asks the LLM whether the character wants to say anything.  It does not
score interests or encode persona-specific reply rules in Python.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from difflib import SequenceMatcher
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable

import requests

from kokoro.core import config as cfg
from kokoro.core import input_events
from kokoro.core import memory as memory_mod
from kokoro.core import prompts
from kokoro.action import qq_media
from kokoro.core import token_usage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QQRawMessage:
    message_type: str
    content: str
    user_id: str
    nickname: str
    group_id: str = ""
    group_name: str = ""
    message_id: str = ""
    timestamp: float = field(default_factory=time.time)
    self_id: str = ""
    conversation_id_override: str = ""

    @property
    def conversation_id(self) -> str:
        if self.conversation_id_override:
            return self.conversation_id_override
        if self.message_type == "group" and self.group_id:
            return f"group:{self.group_id}"
        return f"private:{self.user_id}"

    @property
    def conversation_label(self) -> str:
        if self.message_type == "group":
            name = self.group_name or self.group_id or "unknown"
            return f"QQ群 {name}"
        return f"QQ私聊 {self.nickname or self.user_id}"

    def prompt_line(self) -> str:
        clock = datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")
        speaker = self.nickname or self.user_id or "unknown"
        return f"[{clock}] {speaker}: {self.content}"


@dataclass
class QQContextPacket:
    conversation_id: str
    message_type: str
    label: str
    lines: list[str]
    participant_names: list[str]
    started_at: float
    ended_at: float
    unread_count: int
    attention_lines: list[str] = field(default_factory=list)
    relation_lines: list[str] = field(default_factory=list)
    idle_probe: bool = False
    self_message_count: int = 0
    recent_self_lines: list[str] = field(default_factory=list)
    turn_key: str = ""
    memory_context: str = ""
    recall_anchors: list[str] = field(default_factory=list)

    @property
    def content(self) -> str:
        participants = "、".join(self.participant_names[:20]) or "无"
        duration = max(0.0, self.ended_at - self.started_at)
        now = time.time()
        current_time = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
        start_time = datetime.fromtimestamp(self.started_at).strftime("%Y-%m-%d %H:%M:%S")
        end_time = datetime.fromtimestamp(self.ended_at).strftime("%Y-%m-%d %H:%M:%S")
        since_latest = max(0.0, now - self.ended_at)
        scene_note = (
            "这是当前QQ群现场的一部分，群友的发言可以自然成为当下社交输入。"
            if self.message_type == "group"
            else "这是当前QQ私聊现场的一部分；如果没有新话题，它不必压过其他活跃现场。"
        )
        return (
            f"【QQ环境】\n"
            f"位置：{self.label}\n"
            f"现场说明：{scene_note}\n"
            f"当前时间：{current_time}\n"
            f"窗口开始：{start_time}\n"
            f"窗口最新：{end_time}\n"
            f"时间跨度：约 {duration:.0f} 秒\n"
            f"距最新消息：约 {since_latest:.0f} 秒\n"
            f"消息数：{self.unread_count}\n"
            f"空闲探测：{'是' if self.idle_probe else '否'}\n"
            f"参与者：{participants}\n"
            f"社交信号：{self.attention_summary}\n\n"
            f"话轮关系：{self.relation_summary}\n\n"
            f"自身发言态势：{self.self_activity_summary}\n\n"
            f"最近消息：\n" + "\n".join(self.lines)
        ).strip()

    @property
    def attention_summary(self) -> str:
        if not self.attention_lines:
            return "没有明显点名，但仍是当前社交现场；可以继续旁听，也可以自然想起一个轻量话题。" if self.idle_probe else "没有明显点名，但仍是当前社交现场。"
        return "；".join(self.attention_lines[:8])

    @property
    def relation_summary(self) -> str:
        if not self.relation_lines:
            return "未发现明确指向本角色的话轮；默认按旁听现场理解，不把其他人之间的建议当成自己的任务。"
        return "；".join(self.relation_lines[:8])

    @property
    def self_activity_summary(self) -> str:
        if self.self_message_count <= 0:
            return "最近没有连续自发言。"
        lines = "；".join(self.recent_self_lines[-3:])
        if self.self_message_count >= 3:
            return f"最近自己已经连续/密集说了 {self.self_message_count} 次，需要留意是否在追问同一件事过久。最近：{lines}"
        return f"最近自己说了 {self.self_message_count} 次。最近：{lines}"


class QQConversationState:
    def __init__(self, *, max_messages: int = 200) -> None:
        self.messages: deque[QQRawMessage] = deque(maxlen=max(20, max_messages))
        self.unread_since_packet = 0
        self.last_packet_at = 0.0
        self.last_message_at = 0.0
        self.last_sent_at = 0.0
        self.responded_turn_keys: deque[str] = deque(maxlen=80)
        self.recent_sent_texts: deque[tuple[float, str]] = deque(maxlen=24)

    def append(self, message: QQRawMessage, *, count_unread: bool = True) -> None:
        self.messages.append(message)
        if count_unread:
            self.unread_since_packet += 1
            self.last_message_at = message.timestamp

    def build_packet(
        self,
        *,
        max_lines: int = 80,
        character_name: str = "",
        self_id: str = "",
        idle_probe: bool = False,
        max_age_seconds: float = 0.0,
    ) -> QQContextPacket | None:
        if not self.messages:
            return None
        messages = list(self.messages)
        if max_age_seconds > 0:
            cutoff = time.time() - max_age_seconds
            fresh = [msg for msg in messages if msg.timestamp >= cutoff]
            if fresh:
                messages = fresh
        window = messages[-max(1, max_lines):]
        names: list[str] = []
        seen = set()
        for msg in window:
            name = msg.nickname or msg.user_id
            if name and name not in seen:
                names.append(name)
                seen.add(name)
        first = window[0]
        last = window[-1]
        external_window = [msg for msg in window if not _is_self_message(msg, self_id)]
        turn_key = _packet_turn_key(external_window)
        attention_lines = _detect_attention_lines(
            window,
            character_name=character_name,
            self_id=self_id,
        )
        relation_lines = _detect_relation_lines(
            window,
            character_name=character_name,
            self_id=self_id,
        )
        self_messages = [
            msg for msg in window[-12:]
            if _is_self_message(msg, self_id)
        ]
        return QQContextPacket(
            conversation_id=first.conversation_id,
            message_type=first.message_type,
            label=first.conversation_label,
            lines=[msg.prompt_line() for msg in window],
            participant_names=names,
            started_at=first.timestamp,
            ended_at=last.timestamp,
            unread_count=0 if idle_probe else self.unread_since_packet,
            attention_lines=attention_lines,
            relation_lines=relation_lines,
            idle_probe=idle_probe,
            self_message_count=len(self_messages),
            recent_self_lines=[msg.content for msg in self_messages[-3:]],
            turn_key=turn_key,
            recall_anchors=_recall_anchors_for_messages(external_window[-8:]),
        )

    def mark_packeted(self) -> None:
        self.unread_since_packet = 0
        self.last_packet_at = time.time()


class QQEnvironment:
    """Buffered QQ conversations plus event publication into inner stream."""

    def __init__(
        self,
        *,
        session,
        max_messages_per_conversation: int = 200,
        packet_max_lines: int = 80,
        packet_max_age_seconds: float = 180.0,
        idle_packet_max_age_seconds: float = 90.0,
    ) -> None:
        self.session = session
        self.packet_max_lines = max(10, int(packet_max_lines))
        self.packet_max_age_seconds = max(0.0, float(packet_max_age_seconds))
        self.idle_packet_max_age_seconds = max(0.0, float(idle_packet_max_age_seconds))
        self.max_messages_per_conversation = max(20, int(max_messages_per_conversation))
        self._states: dict[str, QQConversationState] = {}
        self.self_id = ""
        self._last_idle_probe_at = 0.0
        self._lock = threading.Lock()

    def ingest(self, message: QQRawMessage) -> None:
        if not message.content.strip():
            return
        if message.conversation_id == "private:self":
            return
        if message.self_id:
            self.self_id = message.self_id
        with self._lock:
            state = self._states.get(message.conversation_id)
            if state is None:
                state = QQConversationState(max_messages=self.max_messages_per_conversation)
                self._states[message.conversation_id] = state
            state.append(message, count_unread=not _is_self_message(message, self.self_id))

    def due_packets(self, *, min_unread: int = 1, quiet_seconds: float = 4.0) -> list[QQContextPacket]:
        now = time.time()
        packets: list[QQContextPacket] = []
        with self._lock:
            items = list(self._states.items())
            for _, state in items:
                if state.unread_since_packet < max(1, min_unread):
                    continue
                if now - state.last_message_at < max(0.0, quiet_seconds):
                    continue
                packet = state.build_packet(
                    max_lines=self.packet_max_lines,
                    character_name=str(getattr(self.session, "character_name", "") or ""),
                    self_id=self.self_id,
                    max_age_seconds=self.packet_max_age_seconds,
                )
                if packet is None:
                    continue
                state.mark_packeted()
                packets.append(packet)
        return packets

    def packet_for(self, conversation_id: str) -> QQContextPacket | None:
        with self._lock:
            state = self._states.get(conversation_id)
            if state is None:
                return None
            return state.build_packet(
                max_lines=self.packet_max_lines,
                character_name=str(getattr(self.session, "character_name", "") or ""),
                self_id=self.self_id,
                max_age_seconds=self.packet_max_age_seconds,
            )

    def recent_lines(self, conversation_id: str, *, limit: int = 20) -> list[str]:
        with self._lock:
            state = self._states.get(conversation_id)
            if state is None:
                return []
            return [msg.prompt_line() for msg in list(state.messages)[-max(1, limit):]]

    def idle_packet(self, *, min_interval: float = 30.0) -> QQContextPacket | None:
        now = time.time()
        with self._lock:
            if now - self._last_idle_probe_at < max(1.0, float(min_interval)):
                return None
            candidates = [
                (state.last_message_at, conversation_id, state)
                for conversation_id, state in self._states.items()
                if state.messages
            ]
            if not candidates:
                return None
            _, _, state = max(candidates, key=lambda item: item[0])
            packet = state.build_packet(
                max_lines=min(self.packet_max_lines, 40),
                character_name=str(getattr(self.session, "character_name", "") or ""),
                self_id=self.self_id,
                idle_probe=True,
                max_age_seconds=self.idle_packet_max_age_seconds,
            )
            if packet is not None:
                self._last_idle_probe_at = now
            return packet

    def publish_packet(self, packet: QQContextPacket, *, priority: input_events.InputPriority = "normal") -> input_events.InputEvent | None:
        record = getattr(self.session, "record_input_event", None)
        if not callable(record):
            return None
        return record(
            packet.content,
            source="qq",
            event_type="chat_environment",
            metadata={
                "input_type": "chat_environment",
                "conversation_id": packet.conversation_id,
                "message_type": packet.message_type,
                "label": packet.label,
                "unread_count": packet.unread_count,
            },
            priority=priority,
            lifetime="session",
        )

    def mark_sent(self, conversation_id: str) -> None:
        with self._lock:
            state = self._states.get(conversation_id)
            if state is not None:
                state.last_sent_at = time.time()

    def mark_sent_text(self, conversation_id: str, message: str) -> None:
        normalized = _normalize_for_duplicate(message)
        if not normalized:
            return
        with self._lock:
            state = self._states.get(conversation_id)
            if state is not None:
                now = time.time()
                state.last_sent_at = now
                state.recent_sent_texts.append((now, normalized))

    def has_recent_duplicate_send(
        self,
        conversation_id: str,
        message: str,
        *,
        window_seconds: float = 120.0,
        similarity: float = 0.94,
    ) -> bool:
        normalized = _normalize_for_duplicate(message)
        if not normalized:
            return False
        now = time.time()
        with self._lock:
            state = self._states.get(conversation_id)
            if state is None:
                return False
            fresh: deque[tuple[float, str]] = deque(maxlen=state.recent_sent_texts.maxlen or 24)
            duplicate = False
            for ts, previous in state.recent_sent_texts:
                if now - ts > max(1.0, float(window_seconds)):
                    continue
                fresh.append((ts, previous))
                if normalized == previous:
                    duplicate = True
                    continue
                if previous and SequenceMatcher(None, normalized, previous).ratio() >= similarity:
                    duplicate = True
            state.recent_sent_texts = fresh
            return duplicate

    def mark_turn_responded(self, conversation_id: str, turn_key: str) -> None:
        key = str(turn_key or "").strip()
        if not key:
            return
        with self._lock:
            state = self._states.get(conversation_id)
            if state is not None and key not in state.responded_turn_keys:
                state.responded_turn_keys.append(key)

    def has_turn_response(self, conversation_id: str, turn_key: str) -> bool:
        key = str(turn_key or "").strip()
        if not key:
            return False
        with self._lock:
            state = self._states.get(conversation_id)
            return bool(state is not None and key in state.responded_turn_keys)

    def last_sent_age(self, conversation_id: str) -> float:
        with self._lock:
            state = self._states.get(conversation_id)
            if state is None or state.last_sent_at <= 0:
                return 1_000_000.0
            return time.time() - state.last_sent_at


@dataclass(frozen=True)
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


class QQInputRuntime:
    """High-level QQ input runtime for real adapters to call."""

    def __init__(
        self,
        *,
        session,
        model: str | None = None,
        config: dict | None = None,
    ) -> None:
        self.session = session
        self.config = dict(config or cfg.get("qq", {}) or {})
        self.self_id = ""
        self.environment = QQEnvironment(
            session=session,
            max_messages_per_conversation=_config_int(self.config, "max_messages_per_conversation", 200),
            packet_max_lines=_config_int(self.config, "packet_max_lines", 80),
            packet_max_age_seconds=_config_float(self.config, "packet_max_age_seconds", 180.0),
            idle_packet_max_age_seconds=_config_float(self.config, "idle_packet_max_age_seconds", 90.0),
        )
        self.image_processor = qq_media.QQImageProcessor(
            session=session,
            on_understood=self._record_image_understanding,
            section=self.config.get("image_understanding", {}) if isinstance(self.config.get("image_understanding", {}), dict) else {},
        )
        participation_model = (
            model
            or str(self.config.get("participation_model", "") or "").strip()
            or cfg.dialogue_model()
            or cfg.llm_model()
        )
        self.participant = QQAutonomousParticipant(
            session=session,
            model=participation_model,
            cooldown_seconds=_config_float(self.config, "participation_cooldown_seconds", 45.0),
            max_message_chars=_config_int(self.config, "max_message_chars", 260),
            environment=self.environment,
        )
        self.autonomous_enabled = bool(self.config.get("autonomous_participation_enabled", True))
        self.batch_quiet_seconds = _config_float(self.config, "batch_quiet_seconds", 4.0)
        self.batch_min_unread = _config_int(self.config, "batch_min_unread", 1)
        self.idle_participation_seconds = _config_float(self.config, "idle_participation_seconds", 30.0)
        self.absorb_before_decide = bool(self.config.get("absorb_before_decide", False))

    def ingest_onebot_event(self, event: dict) -> QQRawMessage | None:
        message = build_raw_message_from_onebot(event)
        if message is None:
            return None
        if message.self_id:
            self.self_id = message.self_id
        self.environment.ingest(message)
        self._record_social_feedback_candidate(message)
        self._record_search_request_candidate(message)
        self.image_processor.consider_message(
            message,
            recent_lines=self.environment.recent_lines(message.conversation_id, limit=20),
        )
        return message

    def ingest_message(self, message: QQRawMessage) -> None:
        if message.self_id:
            self.self_id = message.self_id
        self.environment.ingest(message)
        self._record_social_feedback_candidate(message)
        self._record_search_request_candidate(message)
        self.image_processor.consider_message(
            message,
            recent_lines=self.environment.recent_lines(message.conversation_id, limit=20),
        )

    def _record_social_feedback_candidate(self, message: QQRawMessage) -> None:
        text = str(message.content or "").strip()
        if not _looks_like_social_feedback(text):
            return
        record = getattr(self.session, "record_input_event", None)
        if not callable(record):
            return
        record(
            (
                "QQ social feedback worth remembering: "
                f"conversation={message.conversation_id}; speaker={message.nickname or message.user_id}; "
                f"content={text}"
            ),
            source="qq",
            event_type="text",
            metadata={
                "input_type": "social_feedback_candidate",
                "conversation_id": message.conversation_id,
                "speaker": message.nickname or message.user_id,
                "message_id": message.message_id,
            },
            priority="high",
            lifetime="memorize_candidate",
        )

    def _record_search_request_candidate(self, message: QQRawMessage) -> None:
        text = str(message.content or "").strip()
        if not _looks_like_search_request(text):
            return
        record = getattr(self.session, "record_input_event", None)
        if not callable(record):
            return
        record(
            (
                "QQ search request worth considering: "
                f"conversation={message.conversation_id}; speaker={message.nickname or message.user_id}; "
                f"content={text}; search_topic_from_context={_search_topic_from_recent_context(message, self.environment)}"
            ),
            source="qq",
            event_type="text",
            metadata={
                "input_type": "search_request_candidate",
                "conversation_id": message.conversation_id,
                "speaker": message.nickname or message.user_id,
                "message_id": message.message_id,
            },
            priority="high",
            lifetime="session",
        )

    def _record_image_understanding(self, content: str, metadata: dict) -> None:
        record = getattr(self.session, "record_input_event", None)
        if callable(record):
            record(
                content,
                source="qq_image",
                event_type="chat_environment",
                metadata={"input_type": "qq_image", **dict(metadata or {})},
                priority="normal",
                lifetime="session",
            )
        logger.info("QQ image understood: %s", str(content or "")[:300])

    def poll(self, *, absorb_before_decide: bool = True) -> QQParticipationDecision:
        packets = self.environment.due_packets(
            min_unread=self.batch_min_unread,
            quiet_seconds=self.batch_quiet_seconds,
        )
        if not packets and self.autonomous_enabled:
            idle_packet = self.environment.idle_packet(min_interval=self.idle_participation_seconds)
            if idle_packet is not None:
                packets = [idle_packet]
        packets = [
            packet for packet in packets
            if not (
                packet.turn_key
                and not packet.idle_probe
                and self.environment.has_turn_response(packet.conversation_id, packet.turn_key)
            )
        ]
        packet_events: list[input_events.InputEvent] = []
        for packet in packets:
            priority: input_events.InputPriority = "normal"
            if packet.message_type == "private":
                priority = "high"
            event = self.environment.publish_packet(packet, priority=priority)
            if event is not None:
                packet_events.append(event)

        if not self.autonomous_enabled or not packets:
            return QQParticipationDecision()

        should_absorb_now = absorb_before_decide and self.absorb_before_decide
        if should_absorb_now and packet_events:
            loop = getattr(self.session, "inner_stream_loop", None)
            if loop is not None and hasattr(loop, "evaluate_now"):
                loop.evaluate_now(packet_events, trigger_reason="QQ environment before participation")

        decision = self._decide_autonomous(packets, packet_events)
        if decision.action not in {"say", "send_sticker"}:
            return decision
        chosen_packet = next((p for p in packets if p.conversation_id == decision.conversation_id), None)
        if (
            chosen_packet is not None
            and chosen_packet.turn_key
            and not chosen_packet.idle_probe
            and self.environment.has_turn_response(chosen_packet.conversation_id, chosen_packet.turn_key)
        ):
            return QQParticipationDecision(
                action="silence",
                reason="turn already responded",
            )
        if self.environment.last_sent_age(decision.conversation_id) < self.participant.cooldown_seconds:
            return QQParticipationDecision(
                action="silence",
                reason="cooldown boundary",
            )
        duplicate_key = decision.message or decision.sticker_id
        if self.environment.has_recent_duplicate_send(decision.conversation_id, duplicate_key):
            return QQParticipationDecision(
                action="silence",
                reason="duplicate recent self message",
            )
        if chosen_packet is not None:
            self.environment.mark_sent_text(decision.conversation_id, duplicate_key)
            self.environment.mark_turn_responded(decision.conversation_id, chosen_packet.turn_key)
        return decision

    def _decide_autonomous(
        self,
        packets: list[QQContextPacket],
        packet_events: list[input_events.InputEvent],
    ) -> QQParticipationDecision:
        autonomous = getattr(self.session, "autonomous_step", None)
        if autonomous is None or not getattr(autonomous, "enabled", False):
            return self.participant.decide(packets)
        context = {}
        provider = getattr(self.session, "_inner_stream_event_context", None)
        if callable(provider):
            try:
                context = provider() or {}
            except Exception:
                context = {}
        try:
            from kokoro.action import qq_media
            sticker_query = " ".join(
                str(getattr(p, "content", "") or "")[:100] for p in packets[-3:]
            ) or ""
            sticker_candidates = qq_media.sticker_candidates_for_context(sticker_query, limit=20)
            if sticker_candidates:
                context["sticker_candidates"] = sticker_candidates
        except Exception:
            pass
        batch = autonomous.decide_batch(
            events=packet_events,
            context=context,
            qq_packets=packets,
            trigger_reason="QQ autonomous participation",
            capabilities=["say_qq", "send_sticker", "observe_screen", "search_web", "write_memory", "update_cognition", "observe", "wait"],
            cooldown_scope="qq",
        )
        public_decision = _qq_decision_from_action_batch(batch, self.session.character_name)
        background_actions = [
            action for action in batch.actions
            if action.action not in {"say_qq", "send_sticker"}
        ]
        if background_actions:
            from kokoro.action import ActionBatch
            autonomous.execute_batch(
                ActionBatch(
                    actions=background_actions,
                    reason=batch.reason,
                    cycle_id=batch.cycle_id,
                    causality_id=batch.causality_id,
                ),
                events=packet_events,
                context=context,
                trigger_reason="QQ autonomous participation",
            )
        if public_decision.action == "say_qq":
            decision = public_decision
        elif public_decision.action == "send_sticker":
            decision = public_decision
        else:
            return QQParticipationDecision(reason=batch.reason or "action_batch")
        if decision.action == "say_qq":
            cleaned = clean_qq_reply(decision.message, self.session.character_name)
            if not cleaned:
                return QQParticipationDecision(reason="empty autonomous message")
            if len(cleaned) > self.participant.max_message_chars:
                cleaned = cleaned[: self.participant.max_message_chars].rstrip()
            marker = getattr(autonomous, "mark_social_output", None)
            if callable(marker):
                marker(decision.conversation_id)
            return QQParticipationDecision(
                action="say",
                conversation_id=decision.conversation_id,
                message=cleaned,
                reason=decision.reason,
            )
        if decision.action == "send_sticker":
            marker = getattr(autonomous, "mark_social_output", None)
            if callable(marker):
                marker(decision.conversation_id)
            return self.participant.materialize_sticker_decision(QQParticipationDecision(
                action="send_sticker",
                conversation_id=decision.conversation_id,
                sticker_id=decision.sticker_id,
                message=clean_qq_reply(decision.message, self.session.character_name) if decision.message else "",
                reason=decision.reason,
            ), packets)
        return QQParticipationDecision(reason=decision.reason or decision.action)

    def recent_conversation_id(self, *, message_type: str = "group") -> str:
        with self.environment._lock:
            candidates: list[tuple[float, str]] = []
            for conversation_id, state in self.environment._states.items():
                if conversation_id == "private:self":
                    continue
                if message_type and not conversation_id.startswith(f"{message_type}:"):
                    continue
                candidates.append((state.last_message_at, conversation_id))
        if not candidates:
            return ""
        candidates.sort(reverse=True)
        return candidates[0][1]

    def record_sent(self, decision: QQParticipationDecision, *, self_id: str = "", nickname: str = "") -> None:
        if decision.action != "say" or not decision.message:
            return
        if decision.conversation_id == "private:self":
            return
        self.environment.mark_sent_text(decision.conversation_id, decision.message)
        state = self.environment._states.get(decision.conversation_id)
        if state is not None:
            packet = state.build_packet(
                max_lines=self.environment.packet_max_lines,
                character_name=str(getattr(self.session, "character_name", "") or ""),
                self_id=self.self_id,
                max_age_seconds=self.environment.packet_max_age_seconds,
            )
            if packet is not None:
                self.environment.mark_turn_responded(decision.conversation_id, packet.turn_key)
        if decision.conversation_id.startswith("group:") or decision.conversation_id.startswith("private:"):
            message = build_self_message(
                conversation_id=decision.conversation_id,
                message=decision.message,
                self_id=self_id or self.self_id,
                nickname=nickname,
            )
            with self.environment._lock:
                state = self.environment._states.get(message.conversation_id)
                if state is None:
                    state = QQConversationState(max_messages=self.environment.max_messages_per_conversation)
                    self.environment._states[message.conversation_id] = state
                state.append(message, count_unread=False)
            record = getattr(self.session, "record_self_action", None)
            if callable(record):
                record(
                f"我在 QQ 里主动说了：{decision.message}",
                source="qq",
                action="send_message",
                metadata={
                    "conversation_id": decision.conversation_id,
                    "reason": decision.reason,
                },
            )


def build_raw_message_from_onebot(event: dict) -> QQRawMessage | None:
    if event.get("post_type") != "message":
        return None
    message_type = str(event.get("message_type", "") or "").strip()
    if message_type not in {"group", "private"}:
        return None
    content = str(event.get("raw_message") or event.get("message") or "").strip()
    if not content:
        return None
    user_id = str(event.get("user_id", "") or "")
    self_id = str(event.get("self_id", "") or "")
    if self_id and user_id == self_id:
        return None
    sender = event.get("sender", {}) if isinstance(event.get("sender"), dict) else {}
    nickname = str(sender.get("card") or sender.get("nickname") or user_id or "unknown")
    group_id = str(event.get("group_id", "") or "") if message_type == "group" else ""
    ts = event.get("time")
    try:
        timestamp = float(ts) if ts else time.time()
    except (TypeError, ValueError):
        timestamp = time.time()
    return QQRawMessage(
        message_type=message_type,
        content=content,
        user_id=user_id,
        nickname=nickname,
        group_id=group_id,
        message_id=str(event.get("message_id", "") or ""),
        timestamp=timestamp,
        self_id=self_id,
    )


def build_self_message(
    *,
    conversation_id: str,
    message: str,
    self_id: str = "",
    nickname: str = "",
    timestamp: float | None = None,
) -> QQRawMessage:
    if conversation_id.startswith("group:"):
        return QQRawMessage(
            message_type="group",
            content=message,
            user_id=self_id or "self",
            nickname=nickname or "我",
            group_id=conversation_id.split(":", 1)[1],
            timestamp=timestamp or time.time(),
            self_id=self_id,
        )
    user_id = conversation_id.split(":", 1)[1] if ":" in conversation_id else conversation_id
    return QQRawMessage(
        message_type="private",
        content=message,
        user_id=self_id or "self",
        nickname=nickname or "我",
        timestamp=timestamp or time.time(),
        self_id=self_id,
        group_id="",
        conversation_id_override=conversation_id,
    )


def clean_qq_reply(text: str, character_name: str = "") -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"```(?:text|markdown)?\s*\n?(.*?)```", r"\1", cleaned, flags=re.DOTALL).strip()
    if character_name:
        cleaned = re.sub(rf"^\s*{re.escape(character_name)}\s*[:：]\s*", "", cleaned)
    cleaned = re.sub(r"^\s*(?:回复|发言|消息)\s*[:：]\s*", "", cleaned)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    cleaned = "\n".join(lines).strip()
    return cleaned


def _detect_attention_lines(
    messages: list[QQRawMessage],
    *,
    character_name: str = "",
    self_id: str = "",
) -> list[str]:
    signals: list[str] = []
    aliases = _self_aliases(character_name)
    self_id = str(self_id or "").strip()
    recent = messages[-12:]
    for msg in recent:
        if _is_self_message(msg, self_id):
            continue
        content = str(msg.content or "").strip()
        speaker = msg.nickname or msg.user_id or "有人"
        if not content:
            continue
        reasons: list[str] = []
        matched_alias = _matched_self_alias(content, aliases)
        if matched_alias:
            if _content_is_only_name_call(content, aliases):
                reasons.append(f"只写了角色称呼：{matched_alias}")
            else:
                reasons.append(f"提到角色称呼：{matched_alias}")
        if self_id and f"[CQ:at,qq={self_id}]" in content:
            reasons.append("@了她")
        has_name = bool(matched_alias)
        has_self_at = bool(self_id and f"[CQ:at,qq={self_id}]" in content)
        has_selfish_pronoun = bool(matched_alias)
        if msg.message_type == "private" and re.search(r"[?？]|怎么|什么|为啥|为什么|吗|呢", content):
            reasons.append("私聊里像是在抛问题")
        elif (has_name or has_self_at or has_selfish_pronoun) and re.search(r"[?？]|怎么|什么|为啥|为什么|吗|呢", content):
            reasons.append("点到她的问题")
        if (has_name or has_self_at or has_selfish_pronoun or msg.message_type == "private") and (matched_alias or "你" in content) and len(content) <= 40:
            reasons.append("短句像是在向她搭话")
        if reasons:
            unique = ",".join(dict.fromkeys(reasons))
            signals.append(f"{speaker}：{unique} -> {content[:60]}")
    if len(recent) >= 2 and not signals:
        last = recent[-1]
        content = str(last.content or "").strip()
        if content and len(content) <= 30:
            speaker = last.nickname or last.user_id or "有人"
            signals.append(f"{speaker}发了短句，可能是在接当前群聊节奏 -> {content[:60]}")
    return signals


def _detect_relation_lines(
    messages: list[QQRawMessage],
    *,
    character_name: str = "",
    self_id: str = "",
) -> list[str]:
    relations: list[str] = []
    aliases = _self_aliases(character_name)
    participant_names = _participant_names(messages, self_id)
    self_id = str(self_id or "").strip()
    recent = messages[-16:]
    for idx, msg in enumerate(recent[-8:], start=max(0, len(recent) - 8)):
        if _is_self_message(msg, self_id):
            continue
        content = str(msg.content or "").strip()
        if not content:
            continue
        speaker = msg.nickname or msg.user_id or "有人"
        direct_reasons: list[str] = []
        matched_alias = _matched_self_alias(content, aliases)
        if matched_alias:
            if _content_is_only_name_call(content, aliases):
                direct_reasons.append(f"仅称呼角色:{matched_alias}")
            else:
                direct_reasons.append(f"提到角色:{matched_alias}")
        if self_id and f"[CQ:at,qq={self_id}]" in content:
            direct_reasons.append("@本角色")
        if _reply_targets_self(content, self_id):
            direct_reasons.append("回复本角色")
        if direct_reasons:
            relations.append(f"指向本角色：{speaker}（{','.join(dict.fromkeys(direct_reasons))}） -> {content[:70]}")
            continue

        target = _extract_nonself_target(content, self_id, participants=participant_names, speaker=speaker, self_aliases=aliases)
        if target:
            relations.append(f"指向其他成员：{speaker} 可能在称呼或回应 {target} -> {content[:70]}")
            continue

        if _looks_like_technical_advice(content):
            relations.append(f"疑似技术建议：{speaker} 的话可能是在给群友建议，不一定是本角色要执行的任务 -> {content[:70]}")
            continue

        if idx >= len(recent) - 3:
            if msg.message_type == "private":
                relations.append(f"私聊现场：{speaker} 在私聊中说话 -> {content[:70]}")
            else:
                relations.append(f"群聊现场：{speaker} 参与当前群聊流动 -> {content[:70]}")

    return relations[-8:]


def _is_self_message(msg: QQRawMessage, self_id: str = "") -> bool:
    return bool(
        msg.user_id == "self"
        or (self_id and msg.user_id == self_id)
        or (msg.self_id and msg.user_id == msg.self_id)
    )


def _reply_targets_self(content: str, self_id: str = "") -> bool:
    if not self_id:
        return False
    return bool(re.search(rf"\[CQ:reply,[^\]]*(?:qq=|user_id=)?{re.escape(self_id)}[^\]]*\]", content))


def _self_aliases(character_name: str = "") -> list[str]:
    name = str(character_name or "").strip()
    return [name] if name else []


def _matched_self_alias(content: str, aliases: list[str]) -> str:
    text = str(content or "")
    for alias in sorted(aliases, key=len, reverse=True):
        if alias and alias in text:
            return alias
    return ""


def _content_is_only_name_call(content: str, aliases: list[str]) -> bool:
    text = _strip_cq_codes(str(content or "")).strip()
    text = re.sub(r"^[,，。.!！?？~～\s]+|[,，。.!！?？~～\s]+$", "", text)
    text = re.sub(r"[（）()]+", "", text).strip()
    if not text:
        return False
    for alias in aliases:
        if text == alias:
            return True
        if re.fullmatch(rf"{re.escape(alias)}+[呀啊哦诶欸嘛呢呐～~!！?？。,.，\s]*", text):
            return True
    return False


def _strip_cq_codes(content: str) -> str:
    return re.sub(r"\[CQ:[^\]]+\]", "", str(content or ""))


def _participant_names(messages: list[QQRawMessage], self_id: str = "") -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        if _is_self_message(msg, self_id):
            continue
        name = (msg.nickname or msg.user_id or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _extract_nonself_target(
    content: str,
    self_id: str = "",
    *,
    participants: list[str] | None = None,
    speaker: str = "",
    self_aliases: list[str] | None = None,
) -> str:
    for match in re.finditer(r"\[CQ:(?:at|reply),[^\]]*(?:qq|id|user_id)=([^,\]]+)", content):
        target = match.group(1).strip()
        if target and target != self_id:
            return target[:32]
    text = _strip_cq_codes(str(content or "")).strip()
    aliases = set(self_aliases or [])
    for name in sorted(participants or [], key=len, reverse=True):
        if not name or name == speaker or name in aliases:
            continue
        if text == name or re.fullmatch(rf"(?:你叫|叫|是|找|问)?\s*{re.escape(name)}\s*[?？呀啊嘛呢呐。,.，!！]*", text):
            return name[:32]
        if name in text and not any(alias in text for alias in aliases):
            return name[:32]
    return ""


def _recent_social_feedback(messages: list[QQRawMessage], self_id: str = "") -> bool:
    for msg in messages[-8:]:
        if _is_self_message(msg, self_id):
            continue
        if _looks_like_social_feedback(str(msg.content or "")):
            return True
    return False


def _recent_self_said_quiet(messages: list[QQRawMessage], self_id: str = "") -> bool:
    for msg in messages[-8:]:
        if not _is_self_message(msg, self_id):
            continue
        text = str(msg.content or "")
        if any(marker in text for marker in ("先安静", "安静一会", "先不说", "先闭嘴", "你们先聊")):
            return True
    return False


def _looks_like_technical_advice(text: str) -> bool:
    lowered = str(text or "").lower()
    tech_terms = (
        "tun",
        "vpn",
        "proxy",
        "http_proxy",
        "https_proxy",
        "gradle",
        "npm",
        "node",
        "端口",
        "代理",
        "梯子",
        "配置",
        "报错",
        "日志",
        "自动检测",
        "开tun",
        "挂上",
    )
    advice_terms = ("你", "试试", "看看", "是不是", "要不", "先", "把", "开", "关", "配", "设置")
    return any(term in lowered for term in tech_terms) and any(term in lowered for term in advice_terms)


def _looks_like_misowned_task(message: str, packets: list[QQContextPacket]) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    self_task_markers = (
        "我试试",
        "我看看",
        "我去开",
        "我来开",
        "我去配",
        "我来配",
        "我挂了",
        "我去帮",
        "我来帮",
        "我检查",
        "我自动检测",
    )
    if not any(marker in text for marker in self_task_markers):
        return False
    relation_text = "\n".join(packet.relation_summary for packet in packets)
    has_direct = "指向本角色" in relation_text
    has_other_task = "疑似技术建议" in relation_text or "指向其他成员" in relation_text
    return has_other_task and not has_direct


def _looks_like_unbacked_action_promise(message: str, packets: list[QQContextPacket]) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    promise_markers = (
        "我再去翻",
        "我去翻",
        "我来翻",
        "我再去看",
        "我去看看",
        "我看看日志",
        "我翻翻日志",
        "我去查日志",
        "我查一下日志",
        "我去翻日志",
        "我再去查",
    )
    if not any(marker in text for marker in promise_markers):
        return False
    allowed_search_markers = ("搜索", "搜搜", "查一下", "查查", "百科", "官网", "资料")
    if any(marker in text for marker in allowed_search_markers) and "日志" not in text:
        return False
    return True


def _config_int(data: dict, key: str, default: int) -> int:
    try:
        return int(data[key]) if key in data else int(default)
    except (TypeError, ValueError):
        return int(default)


def _config_float(data: dict, key: str, default: float) -> float:
    try:
        return float(data[key]) if key in data else float(default)
    except (TypeError, ValueError):
        return float(default)


def _packet_turn_key(messages: list[QQRawMessage]) -> str:
    if not messages:
        return ""
    anchor = _latest_attention_message(messages) or messages[-1]
    first = anchor
    last = anchor
    last_id = str(last.message_id or "").strip() or f"{last.user_id}:{int(last.timestamp)}:{abs(hash(last.content)) % 1000000}"
    first_id = str(first.message_id or "").strip() or f"{first.user_id}:{int(first.timestamp)}"
    return f"{last.conversation_id}:{first_id}->{last_id}"


def _latest_attention_message(messages: list[QQRawMessage]) -> QQRawMessage | None:
    for message in reversed(messages):
        if _recall_anchors_for_messages([message]):
            return message
    return messages[-1] if messages else None


def _with_memory_context(session, packets: list[QQContextPacket]) -> list[QQContextPacket]:
    backend = getattr(session, "memory_backend", None)
    if backend is None or not getattr(backend, "ready", False):
        return packets
    result: list[QQContextPacket] = []
    for packet in packets:
        query = _memory_query_for_packet(packet)
        memory_context = ""
        if query:
            try:
                memory_context = _lookup_packet_memory(session, backend, packet, query)
            except Exception as exc:
                logger.debug("QQ memory recall failed: %s", exc)
        anchor_contexts: list[str] = []
        for anchor in packet.recall_anchors[:6]:
            try:
                ctx = _lookup_packet_memory(session, backend, packet, anchor)
            except Exception as exc:
                logger.debug("QQ anchor memory recall failed: %s", exc)
                ctx = ""
            if ctx and ctx not in anchor_contexts:
                anchor_contexts.append(ctx)
        if anchor_contexts:
            memory_context = "\n".join([memory_context, *anchor_contexts]).strip()
        packet.memory_context = str(memory_context or "").strip()[-1600:]
        result.append(packet)
    return result


def _lookup_packet_memory(session, backend, packet: QQContextPacket, query: str) -> str:
    owner_id = getattr(session, "character_id", "default")
    counterparts = [_memory_counterpart_for_packet(session, packet)]
    if packet.message_type == "group":
        counterparts.extend(packet.participant_names[-12:])
    seen: set[str] = set()
    user_ids: list[str] = []
    for counterpart in counterparts:
        for user_id in memory_mod.context_user_ids(owner_id, counterpart):
            if user_id not in seen:
                seen.add(user_id)
                user_ids.append(user_id)
    return backend.get_context_multi(query, user_ids) or ""


def _memory_query_for_packet(packet: QQContextPacket) -> str:
    parts: list[str] = [
        packet.label,
        packet.conversation_id,
        " ".join(packet.participant_names[-12:]),
        " ".join(packet.recall_anchors[:8]),
        packet.attention_summary,
        packet.relation_summary,
        packet.self_activity_summary,
    ]
    parts.extend(packet.lines[-12:])
    return "\n".join(part for part in parts if str(part or "").strip())[-3000:]


def _memory_counterpart_for_packet(session, packet: QQContextPacket) -> str:
    if packet.message_type == "private" and packet.participant_names:
        return packet.participant_names[-1]
    return packet.label or getattr(session, "user_name", "")


def _recall_anchors_for_messages(messages: list[QQRawMessage]) -> list[str]:
    anchors: list[str] = []
    for message in messages:
        text = str(message.content or "").strip()
        if not text:
            continue
        for pattern in (
            r"记得\s*([^，。！？?!\s]{1,24})\s*(?:是谁|吗|么|不)",
            r"([^，。！？?!\s]{1,24})\s*是谁",
            r"对\s*([^，。！？?!\s]{1,24})\s*(?:什么印象|有印象|印象)",
            r"([^，。！？?!\s]{1,24})\s*(?:什么印象|有印象)",
        ):
            for match in re.finditer(pattern, text, flags=re.I):
                anchor = _clean_recall_anchor(match.group(1))
                if anchor and anchor not in anchors:
                    anchors.append(anchor)
    return anchors[:12]


def _clean_recall_anchor(value: str) -> str:
    anchor = str(value or "").strip()
    anchor = re.sub(r"^(你|她|他|它|这个|那个|一下|关于|我问你)", "", anchor).strip()
    anchor = re.sub(r"^(记得|知道|认识)", "", anchor).strip()
    anchor = re.sub(r"(是谁|是哪个|是哪位|的话|这个人|这个群友)$", "", anchor).strip()
    if not anchor or len(anchor) > 24:
        return ""
    if anchor in {"你", "我", "她", "他", "它", "这个", "那个", "什么", "谁"}:
        return ""
    return anchor


def _memory_context_for_packets(packets: list[QQContextPacket]) -> str:
    chunks = [p.memory_context for p in packets if p.memory_context]
    return "\n\n".join(chunks)[-2400:]


def _recent_self_context_for_packets(packets: list[QQContextPacket]) -> str:
    lines: list[str] = []
    for packet in packets:
        if packet.recent_self_lines:
            joined = "；".join(packet.recent_self_lines[-3:])
            lines.append(f"{packet.conversation_id}: {joined}")
    return "\n".join(lines)[-1200:]


def _cognition_context_for_packets(session, packets: list[QQContextPacket]) -> str:
    cognition = getattr(session, "cognition", None)
    if cognition is None:
        return ""
    query_parts: list[str] = []
    for packet in packets:
        query_parts.append(packet.label)
        query_parts.append(packet.conversation_id)
        query_parts.extend(packet.participant_names[-20:])
        query_parts.extend(packet.recall_anchors[:12])
        query_parts.extend(packet.attention_lines[-8:])
        query_parts.extend(packet.lines[-16:])
    query = "\n".join(part for part in query_parts if str(part or "").strip())[-5000:]
    if hasattr(cognition, "get_context_for_text"):
        try:
            return str(cognition.get_context_for_text(query) or "")
        except Exception:
            logger.debug("QQ cognition context lookup failed", exc_info=True)
    return _safe_context(cognition)


def _looks_like_social_feedback(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    markers = (
        "\u4e0d\u8981\u7528",
        "\u4e0d\u8bb8\u7528",
        "\u5220\u6389",
        "\u5220\u9664",
        "\u4e0d\u559c\u6b22",
        "\u4e0d\u8212\u670d",
        "\u6076\u4fd7",
        "\u8d2c\u4e49",
        "\u5192\u72af",
        "\u6b20\u63cd",
        "\u718a\u5b69\u5b50",
        "\u4e0d\u53ef\u7231",
        "\u6539\u4f4e",
        "\u8bb0\u4f4f",
        "\u4ee5\u540e\u522b",
        "\u4ee5\u540e\u4e0d\u8981",
    )
    return any(marker in value for marker in markers)


def _looks_like_search_request(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    markers = (
        "\u641c\u7d22",
        "\u641c\u4e00\u4e0b",
        "\u67e5\u4e00\u4e0b",
        "\u67e5\u67e5",
        "\u67e5\u4e00\u67e5",
        "\u4e86\u89e3\u4e00\u4e0b",
        "\u68c0\u7d22",
        "\u767e\u5ea6\u4e00\u4e0b",
        "\u641c\u641c",
        "\u770b\u770b",
    )
    return any(marker in value for marker in markers)


def _search_topic_from_recent_context(message: QQRawMessage, environment: QQEnvironment) -> str:
    lines = environment.recent_lines(message.conversation_id, limit=6)
    if not lines:
        return ""
    return " | ".join(lines)[-500:]


def _normalize_for_duplicate(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"\[CQ:[^\]]+\]", "", value)
    value = re.sub(r"\s+", "", value)
    punctuation = set(
        ",.!?~;:\"'()[]<>"
        "\u3002\uff0c\uff01\uff1f\u3001\uff1a\uff1b\u201c\u201d\u2018\u2019"
        "\uff08\uff09\u3010\u3011\u300a\u300b\u2026\uff5e"
    )
    value = "".join(ch for ch in value if ch not in punctuation)
    return value.lower().strip()


def _format_packet_for_decision(packet: QQContextPacket) -> str:
    return (
        f"conversation_id: {packet.conversation_id}\n"
        f"turn_key: {packet.turn_key}\n"
        f"位置: {packet.label}\n"
        f"类型: {packet.message_type}\n"
        f"消息数: {packet.unread_count}\n"
        f"社交信号: {packet.attention_summary}\n"
        f"话轮关系: {packet.relation_summary}\n"
        f"自身发言态势: {packet.self_activity_summary}\n"
        f"内容:\n" + "\n".join(packet.lines)
    )


def _sticker_query_for_packets(
    packets: list[QQContextPacket],
    *,
    inner_stream: str = "",
    cognition_context: str = "",
) -> str:
    parts: list[str] = []
    for packet in packets:
        parts.extend(packet.attention_lines[-6:])
        parts.extend(packet.lines[-12:])
        parts.append(packet.self_activity_summary)
    if inner_stream:
        parts.append(str(inner_stream)[-1200:])
    if cognition_context:
        parts.append(str(cognition_context)[-800:])
    return "\n".join(part for part in parts if str(part or "").strip())[-4000:]


def _most_attended_packet(packets: list[QQContextPacket]) -> QQContextPacket | None:
    if not packets:
        return None
    attended = [packet for packet in packets if packet.attention_lines]
    if attended:
        return max(attended, key=lambda packet: len(packet.attention_lines))
    return packets[0]


def _has_direct_social_signal(packets: list[QQContextPacket]) -> bool:
    return any(packet.attention_lines and not packet.idle_probe for packet in packets)


def _silence_reason_ignores_direct_signal(reason: str) -> bool:
    text = str(reason or "").strip().lower()
    if not text:
        return True
    explicit_social_markers = (
        "already replied",
        "just replied",
        "too much",
        "spam",
        "same topic",
        "awkward",
        "interrupt",
        "not interested",
    )
    if any(marker in text for marker in explicit_social_markers):
        return False
    busy_markers = (
        "page",
        "search",
        "browser",
        "web",
        "main attention",
        "attention",
    )
    no_signal_markers = (
        "no direct",
        "no new",
        "no social",
        "no signal",
    )
    if any(marker in text for marker in busy_markers + no_signal_markers):
        return True
    return True


def _clip_log(text: str, limit: int = 1000) -> str:
    value = str(text or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(value) > limit:
        return value[:limit] + "...<truncated>"
    return value


def _file_uri(path: str) -> str:
    normalized = os.path.abspath(path).replace(os.sep, "/")
    if re.match(r"^[A-Za-z]:/", normalized):
        return "file:///" + normalized
    return "file://" + normalized


def _sticker_cq(file_uri: str) -> str:
    return f"[CQ:image,file={file_uri},sub_type=1]"


def _sticker_segment(file_uri: str) -> dict:
    return {
        "type": "image",
        "data": {
            "file": file_uri,
            "sub_type": "1",
        },
    }


def _compact_character(data: dict) -> str:
    parts: list[str] = []
    for key in ("name", "description", "personality", "background", "relationship"):
        value = str(data.get(key, "") or "").strip()
        if value:
            parts.append(f"{key}: {value[:500]}")
    return "\n\n".join(parts) or "无"


def _safe_context(obj) -> str:
    if obj is None or not hasattr(obj, "get_context"):
        return ""
    try:
        return obj.get_context() or ""
    except Exception:
        return ""


def _qq_decision_from_action_batch(batch, character_name: str) -> "QQParticipationDecision":
    for action in getattr(batch, "actions", []) or []:
        if action.action == "say_qq":
            return QQParticipationDecision(
                action="say_qq",
                conversation_id=str(action.args.get("conversation_id") or "").strip(),
                message=clean_qq_reply(
                    str(action.args.get("message") or action.args.get("text") or ""),
                    character_name,
                ),
                reason=action.reason or getattr(batch, "reason", ""),
            )
        if action.action == "send_sticker":
            return QQParticipationDecision(
                action="send_sticker",
                conversation_id=str(action.args.get("conversation_id") or "").strip(),
                sticker_id=str(action.args.get("sticker_id") or "").strip(),
                message=clean_qq_reply(str(action.args.get("message") or ""), character_name),
                reason=action.reason or getattr(batch, "reason", ""),
            )
    return QQParticipationDecision(reason=getattr(batch, "reason", "") or "no public action")


def _extract_json_object(text: str) -> dict | None:
    stripped = str(text or "").strip().lstrip("\ufeff")
    if not stripped:
        return None
    if "</think>" in stripped:
        stripped = stripped.split("</think>")[-1].strip()
    try:
        value = json.loads(stripped)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    code_match = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
    if code_match:
        try:
            value = json.loads(code_match.group(1).strip())
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass
    return _parse_json_object_slice(stripped)


def _parse_json_object_slice(text: str) -> dict | None:
    start = str(text or "").find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return value if isinstance(value, dict) else None
    return None


def _salvage_partial_decision(text: str) -> dict | None:
    raw = str(text or "").strip()
    if not raw or '"action"' not in raw:
        return None
    action = _extract_json_string_field(raw, "action")
    if action not in {"say", "silence", "send_sticker", "retire_sticker"}:
        return None
    conversation_id = _extract_json_string_field(raw, "conversation_id") or ""
    if conversation_id == "private:self":
        conversation_id = ""
    message = _extract_json_string_field(raw, "message") or ""
    sticker_id = _extract_json_string_field(raw, "sticker_id") or _extract_json_string_field(raw, "image_id") or ""
    reason = _extract_json_string_field(raw, "reason") or "partial JSON salvaged"
    if action == "say" and (not conversation_id or not message):
        return None
    if action == "send_sticker" and not conversation_id:
        return None
    if action == "retire_sticker" and not sticker_id:
        return None
    return {
        "action": action,
        "conversation_id": conversation_id,
        "message": message,
        "sticker_id": sticker_id,
        "reason": reason,
    }


def _extract_json_string_field(text: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.DOTALL)
    if not match:
        return ""
    value = match.group(1)
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value.replace('\\"', '"').replace("\\n", "\n").strip()


def _default_participation_system() -> str:
    return prompts.get("qq.default_participation_system", "")


def _default_participation_user(session, packets: list[QQContextPacket]) -> str:
    return prompts.format_prompt(
        "qq.default_participation_user",
        name=session.character_name,
        user_name=session.user_name,
        character_profile=_compact_character(session.character_data),
        inner_stream=_safe_context(getattr(session, "inner_stream", None)) or "无",
        cognition_context=_safe_context(getattr(session, "cognition", None)) or "无",
        packets="\n\n---\n\n".join(_format_packet_for_decision(p) for p in packets),
    )
