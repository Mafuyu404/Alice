"""QQ participation decision formatting and media helpers."""

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

from kokoro.action.tools.qq.parsing import clean_qq_reply
from kokoro.action.tools.qq.social_signals import _format_packet_for_decision


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
