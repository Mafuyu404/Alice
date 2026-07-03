"""QQ content guard heuristics."""

from __future__ import annotations

import re

from kokoro.action.tools.qq.models import QQContextPacket, QQRawMessage
from kokoro.action.tools.qq.social_identity import _is_self_message


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
