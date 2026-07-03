"""QQ turn keys, recall anchors, and decision formatting."""

from __future__ import annotations

import re

from kokoro.action.tools.qq.models import QQContextPacket, QQRawMessage


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
