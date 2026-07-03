"""QQ memory and cognition context lookup helpers."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import timezone

from kokoro.core import memory as memory_mod
from kokoro.core import prompts
from kokoro.action.tools.qq import media as qq_media
from kokoro.action.tools.qq.models import QQContextPacket, QQRawMessage

logger = logging.getLogger(__name__)

from kokoro.action.tools.qq.social_signals import _recall_anchors_for_messages
from kokoro.action.tools.qq.parsing import _safe_context


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
