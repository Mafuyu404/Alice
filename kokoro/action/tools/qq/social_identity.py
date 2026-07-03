"""QQ speaker identity and CQ target helpers."""

from __future__ import annotations

import re

from kokoro.action.tools.qq.models import QQRawMessage


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
