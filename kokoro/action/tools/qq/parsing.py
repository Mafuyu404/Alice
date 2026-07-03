"""QQ event parsing, text cleanup, and JSON extraction helpers."""

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


def _clip_log(text: str, limit: int = 1000) -> str:
    value = str(text or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(value) > limit:
        return value[:limit] + "...<truncated>"
    return value


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
