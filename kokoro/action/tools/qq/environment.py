"""QQ conversation buffering and environment packet publication."""

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

from kokoro.action.tools.qq.models import QQContextPacket, QQRawMessage
from kokoro.action.tools.qq.helpers import (
    _detect_attention_lines,
    _detect_relation_lines,
    _is_self_message,
    _normalize_for_duplicate,
    _packet_turn_key,
    _recall_anchors_for_messages,
)


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
