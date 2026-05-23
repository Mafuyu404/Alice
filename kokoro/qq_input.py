"""QQ environment input and autonomous participation helpers.

This module keeps QQ-specific runtime logic thin: it buffers raw chat messages,
formats recent group/private context as natural-language environment packets,
and asks the LLM whether the character wants to say anything.  It does not
score interests or encode persona-specific reply rules in Python.
"""

from __future__ import annotations

import json
import logging
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

    @property
    def content(self) -> str:
        participants = "、".join(self.participant_names[:20]) or "无"
        duration = max(0.0, self.ended_at - self.started_at)
        return (
            f"【QQ环境】\n"
            f"位置：{self.label}\n"
            f"时间跨度：约 {duration:.0f} 秒\n"
            f"消息数：{self.unread_count}\n"
            f"参与者：{participants}\n\n"
            f"最近消息：\n" + "\n".join(self.lines)
        ).strip()


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

    def build_packet(self, *, max_lines: int = 80) -> QQContextPacket | None:
        if not self.messages:
            return None
        window = list(self.messages)[-max(1, max_lines):]
        names: list[str] = []
        seen = set()
        for msg in window:
            name = msg.nickname or msg.user_id
            if name and name not in seen:
                names.append(name)
                seen.add(name)
        first = window[0]
        last = window[-1]
        return QQContextPacket(
            conversation_id=first.conversation_id,
            message_type=first.message_type,
            label=first.conversation_label,
            lines=[msg.prompt_line() for msg in window],
            participant_names=names,
            started_at=first.timestamp,
            ended_at=last.timestamp,
            unread_count=self.unread_since_packet,
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
    ) -> None:
        self.session = session
        self.packet_max_lines = max(10, int(packet_max_lines))
        self.max_messages_per_conversation = max(20, int(max_messages_per_conversation))
        self._states: dict[str, QQConversationState] = {}
        self._lock = threading.Lock()

    def ingest(self, message: QQRawMessage) -> None:
        if not message.content.strip():
            return
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
                packet = state.build_packet(max_lines=self.packet_max_lines)
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
            return state.build_packet(max_lines=self.packet_max_lines)

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

    @classmethod
    def from_dict(cls, data: dict) -> "QQParticipationDecision":
        action = str(data.get("action", "silence") or "silence").strip().lower()
        if action not in {"silence", "say"}:
            action = "silence"
        message = str(data.get("message", "") or "").strip()
        if action == "say" and not message:
            action = "silence"
        return cls(
            action=action,
            conversation_id=str(data.get("conversation_id", "") or "").strip(),
            message=message,
            reason=str(data.get("reason", "") or "").strip(),
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
        packet_list = list(packets)
        if not packet_list:
            return QQParticipationDecision()

        system_prompt = prompts.get("qq.participation_system", "") or _default_participation_system()
        user_prompt = prompts.format_prompt(
            "qq.participation_user",
            name=self.session.character_name,
            user_name=self.session.user_name,
            character_profile=_compact_character(self.session.character_data),
            inner_stream=_safe_context(getattr(self.session, "inner_stream", None)) or "无",
            cognition_context=_safe_context(getattr(self.session, "cognition", None)) or "无",
            summary=getattr(self.session, "summary", "") or "无",
            packets="\n\n---\n\n".join(_format_packet_for_decision(p) for p in packet_list),
        ) or _default_participation_user(self.session, packet_list)

        try:
            raw = self._call_llm(system_prompt, user_prompt)
            data = _extract_json_object(raw)
            if not data:
                raise ValueError("QQ participation LLM did not return JSON object")
            decision = QQParticipationDecision.from_dict(data)
        except Exception as exc:
            logger.warning("QQ participation decision failed: %s", exc)
            return QQParticipationDecision(reason=type(exc).__name__)

        valid_ids = {p.conversation_id for p in packet_list}
        if decision.action == "say" and decision.conversation_id not in valid_ids:
            return QQParticipationDecision(reason="LLM chose an unknown conversation")
        if decision.action == "say":
            cleaned = clean_qq_reply(decision.message, self.session.character_name)
            if not cleaned:
                return QQParticipationDecision(reason="empty cleaned message")
            if len(cleaned) > self.max_message_chars:
                cleaned = cleaned[: self.max_message_chars].rstrip()
            return QQParticipationDecision(
                action="say",
                conversation_id=decision.conversation_id,
                message=cleaned,
                reason=decision.reason,
            )
        return decision

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
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
            resp = requests.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.5,
                    "max_tokens": 420,
                    "response_format": {"type": "json_object"},
                },
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
                "options": {"temperature": 0.5, "num_predict": 420},
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
        self.environment = QQEnvironment(
            session=session,
            max_messages_per_conversation=_config_int(self.config, "max_messages_per_conversation", 200),
            packet_max_lines=_config_int(self.config, "packet_max_lines", 80),
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

    def ingest_onebot_event(self, event: dict) -> QQRawMessage | None:
        message = build_raw_message_from_onebot(event)
        if message is None:
            return None
        self.environment.ingest(message)
        return message

    def ingest_message(self, message: QQRawMessage) -> None:
        self.environment.ingest(message)

    def poll(self, *, absorb_before_decide: bool = True) -> QQParticipationDecision:
        packets = self.environment.due_packets(
            min_unread=self.batch_min_unread,
            quiet_seconds=self.batch_quiet_seconds,
        )
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

        if absorb_before_decide and packet_events:
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
        self.environment.mark_sent(decision.conversation_id)
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
        f"内容:\n" + "\n".join(packet.lines)
    )


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
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(stripped[start : end + 1])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _default_participation_system() -> str:
    return (
        "你是角色在 QQ 环境中的自主参与判断器。程序只把群聊和私聊环境交给你，"
        "不要把这理解成客服机器人或被 @ 才能回复的机器人。\n\n"
        "你要从角色自身的注意力、兴趣、状态、社交节奏和上下文自然度出发，决定现在是否说话。"
        "可以沉默、继续旁听，也可以在感兴趣时自然插话、回应某个人，或主动开启一个轻量话题。\n\n"
        "只返回 JSON："
        '{"action":"silence|say","conversation_id":"要发往的 conversation_id，沉默时为空",'
        '"message":"角色真正要发到 QQ 的话，沉默时为空","reason":"简短理由"}'
    )


def _default_participation_user(session, packets: list[QQContextPacket]) -> str:
    return (
        f"角色：{session.character_name}\n"
        f"对话对象名：{session.user_name}\n\n"
        f"角色资料：\n{_compact_character(session.character_data)}\n\n"
        f"内在叙事流：\n{_safe_context(getattr(session, 'inner_stream', None)) or '无'}\n\n"
        f"认知上下文：\n{_safe_context(getattr(session, 'cognition', None)) or '无'}\n\n"
        f"QQ 环境包：\n"
        + "\n\n---\n\n".join(_format_packet_for_decision(p) for p in packets)
        + "\n\n如果没有自然想说的话就 silence。若要说，只写会直接发到 QQ 的内容。"
    )
