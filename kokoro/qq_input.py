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
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable

import requests

from kokoro import config as cfg
from kokoro import input_events
from kokoro import prompts
from kokoro import qq_media
from kokoro import token_usage

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

    @property
    def conversation_id(self) -> str:
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

    @property
    def content(self) -> str:
        participants = "、".join(self.participant_names[:20]) or "无"
        duration = max(0.0, self.ended_at - self.started_at)
        scene_note = (
            "这是当前QQ群现场的一部分，群友的发言可以自然成为当下社交输入。"
            if self.message_type == "group"
            else "这是当前QQ私聊现场的一部分；如果没有新话题，它不必压过其他活跃现场。"
        )
        return (
            f"【QQ环境】\n"
            f"位置：{self.label}\n"
            f"现场说明：{scene_note}\n"
            f"时间跨度：约 {duration:.0f} 秒\n"
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
            return "未发现明确指向雪吱的话轮；默认按旁听现场理解，不把群友之间的建议当成自己的任务。"
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

    def append(self, message: QQRawMessage) -> None:
        self.messages.append(message)
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
            if (self_id and msg.user_id == self_id) or (msg.self_id and msg.user_id == msg.self_id)
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
            state.append(message)

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
        if action not in {"silence", "say", "send_sticker"}:
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
    ) -> None:
        self.session = session
        self.model = model
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.max_message_chars = max(40, int(max_message_chars))

    def decide(self, packets: Iterable[QQContextPacket]) -> QQParticipationDecision:
        packet_list = [packet for packet in packets if packet.conversation_id != "private:self"]
        if not packet_list:
            return QQParticipationDecision()

        system_prompt = prompts.get("qq.participation_system", "") or prompts.get("qq.default_participation_system", "")
        system_prompt = (
            f"{system_prompt}\n\n"
            f"{prompts.get('qq.participation_output_contract', '')}"
        )
        sticker_query = _sticker_query_for_packets(
            packet_list,
            inner_stream=_safe_context(getattr(self.session, "inner_stream", None)),
            cognition_context=_safe_context(getattr(self.session, "cognition", None)),
        )
        user_prompt = prompts.format_prompt(
            "qq.participation_user",
            name=self.session.character_name,
            user_name=self.session.user_name,
            character_profile=_compact_character(self.session.character_data),
            inner_stream=_safe_context(getattr(self.session, "inner_stream", None)) or "无",
            cognition_context=_safe_context(getattr(self.session, "cognition", None)) or "无",
            summary=getattr(self.session, "summary", "") or "无",
            sticker_candidates=qq_media.sticker_candidates_for_context(sticker_query, limit=30),
            packets="\n\n---\n\n".join(_format_packet_for_decision(p) for p in packet_list),
        ) or _default_participation_user(self.session, packet_list)
        user_prompt = (
            f"{user_prompt}\n\n"
            f"{prompts.get('qq.participation_user_suffix', '')}"
        )

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
        if decision.action == "send_sticker":
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
                fallback = self._fallback_direct_reply(packet_list, system_prompt)
                if fallback.action == "say":
                    return fallback
                return QQParticipationDecision(reason="unknown sticker_id")
            sticker_id = str(sticker_item.get("id") or decision.sticker_id)
            path = qq_media.resolve_sticker_path(sticker_id)
            if not path:
                return QQParticipationDecision(reason="sticker path missing")
            file_uri = _file_uri(path)
            cq = _sticker_cq(file_uri)
            message = (decision.message + " " + cq).strip() if decision.message else cq
            segments: list[dict] = []
            if decision.message:
                segments.append({"type": "text", "data": {"text": decision.message}})
            segments.append(_sticker_segment(file_uri))
            return QQParticipationDecision(
                action="say",
                conversation_id=decision.conversation_id,
                message=message,
                reason=decision.reason,
                sticker_id=sticker_id,
                message_segments=segments,
            )
        if decision.action == "say":
            cleaned = clean_qq_reply(decision.message, self.session.character_name)
            if not cleaned:
                return QQParticipationDecision(reason="empty cleaned message")
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
            if not message or message.upper() == "SILENCE":
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
        model = self.model
        openai_compatible = False
        if cfg.is_deepseek_model(model):
            api_key = cfg.deepseek_api_key()
            api_url = cfg.deepseek_url()
            openai_compatible = True
        else:
            api_key = ""
            api_url = cfg.llm_url()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        headers = {"Content-Type": "application/json"}
        if openai_compatible:
            base_url = api_url.rstrip("/")
            if not re.search(r"/v\d+$", base_url):
                base_url += "/v1"
            headers["Authorization"] = f"Bearer {api_key}"
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 512,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            resp = requests.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=45,
            )
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("usage", {})
            pt = int(usage.get("prompt_tokens", 0))
            ct = int(usage.get("completion_tokens", 0))
            if pt or ct:
                token_usage.record(model, "qq_participation", pt, ct)
            return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        resp = requests.post(
            f"{api_url}/api/chat",
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
        self.environment.ingest(message)
        self.image_processor.consider_message(
            message,
            recent_lines=self.environment.recent_lines(message.conversation_id, limit=20),
        )
        return message

    def ingest_message(self, message: QQRawMessage) -> None:
        if message.self_id:
            self.self_id = message.self_id
        self.environment.ingest(message)
        self.image_processor.consider_message(
            message,
            recent_lines=self.environment.recent_lines(message.conversation_id, limit=20),
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
        if _has_direct_social_signal(packets):
            should_absorb_now = False
        if should_absorb_now and packet_events:
            loop = getattr(self.session, "inner_stream_loop", None)
            if loop is not None and hasattr(loop, "evaluate_now"):
                loop.evaluate_now(packet_events, trigger_reason="QQ environment before participation")

        decision = self.participant.decide(packets)
        if decision.action != "say":
            return decision
        if self.environment.last_sent_age(decision.conversation_id) < self.participant.cooldown_seconds:
            return QQParticipationDecision(
                action="silence",
                reason="cooldown boundary",
            )
        return decision

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
        self.environment.mark_sent(decision.conversation_id)
        if decision.conversation_id.startswith("group:") or decision.conversation_id.startswith("private:"):
            self.environment.ingest(
                build_self_message(
                    conversation_id=decision.conversation_id,
                    message=decision.message,
                    self_id=self_id,
                    nickname=nickname,
                )
            )
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
    name = str(character_name or "").strip()
    self_id = str(self_id or "").strip()
    recent = messages[-12:]
    for msg in recent:
        content = str(msg.content or "").strip()
        speaker = msg.nickname or msg.user_id or "有人"
        if not content:
            continue
        reasons: list[str] = []
        if name and name in content:
            reasons.append(f"点名{name}")
        if self_id and f"[CQ:at,qq={self_id}]" in content:
            reasons.append("@了她")
        if re.search(r"[?？]|怎么|什么|为啥|为什么|吗|呢", content):
            reasons.append("像是在抛问题")
        if re.search(r"雪吱|小雪|吱吱|你", content) and len(content) <= 40:
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
    name = str(character_name or "").strip()
    self_id = str(self_id or "").strip()
    recent = messages[-16:]
    last_self_index = -1
    for idx, msg in enumerate(recent):
        if _is_self_message(msg, self_id):
            last_self_index = idx

    for idx, msg in enumerate(recent[-8:], start=max(0, len(recent) - 8)):
        if _is_self_message(msg, self_id):
            continue
        content = str(msg.content or "").strip()
        if not content:
            continue
        speaker = msg.nickname or msg.user_id or "有人"
        direct_reasons: list[str] = []
        if name and name in content:
            direct_reasons.append(f"mentions {name}")
        if self_id and f"[CQ:at,qq={self_id}]" in content:
            direct_reasons.append("@self")
        if _reply_targets_self(content, self_id):
            direct_reasons.append("reply_to_self")
        if last_self_index >= 0 and 0 < idx - last_self_index <= 3 and len(content) <= 80:
            direct_reasons.append("near_self_turn")
        if direct_reasons:
            relations.append(f"direct_to_self: {speaker} ({','.join(dict.fromkeys(direct_reasons))}) -> {content[:70]}")
            continue

        target = _extract_nonself_target(content, self_id)
        if target:
            relations.append(f"other_thread: {speaker} is addressing/replying to another QQ member ({target}) -> {content[:70]}")
            continue

        if _looks_like_technical_advice(content):
            relations.append(f"technical_advice_not_self_task: {speaker} may be advising another member; Alice should not answer as executor -> {content[:70]}")
            continue

        if idx >= len(recent) - 3:
            relations.append(f"ambient_group_topic: {speaker} contributes to the current group flow -> {content[:70]}")

    return relations[-8:]


def _is_self_message(msg: QQRawMessage, self_id: str = "") -> bool:
    return bool((self_id and msg.user_id == self_id) or (msg.self_id and msg.user_id == msg.self_id))


def _reply_targets_self(content: str, self_id: str = "") -> bool:
    if not self_id:
        return False
    return bool(re.search(rf"\[CQ:reply,[^\]]*(?:qq=|user_id=)?{re.escape(self_id)}[^\]]*\]", content))


def _extract_nonself_target(content: str, self_id: str = "") -> str:
    for match in re.finditer(r"\[CQ:(?:at|reply),[^\]]*(?:qq|id|user_id)=([^,\]]+)", content):
        target = match.group(1).strip()
        if target and target != self_id:
            return target[:32]
    return ""


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
    has_direct = "direct_to_self" in relation_text
    has_other_task = "technical_advice_not_self_task" in relation_text or "other_thread" in relation_text
    return has_other_task and not has_direct


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


def _format_packet_for_decision(packet: QQContextPacket) -> str:
    return (
        f"conversation_id: {packet.conversation_id}\n"
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
    if action not in {"say", "silence", "send_sticker"}:
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
