"""QQ sticker library helpers and sticker quality guards."""

from __future__ import annotations

import re

from kokoro.action.tools.qq.media_models import QQImageUnderstanding
from kokoro.action.tools.qq.media_utils import _string_list
from kokoro.action.tools.qq.sticker_library import StickerLibrary


def sticker_candidates_text(limit: int = 18) -> str:
    return StickerLibrary().candidates_text(limit=limit)


def sticker_candidates_for_context(context: str, limit: int = 30) -> str:
    return StickerLibrary().search_candidates(context, limit=limit)


def resolve_sticker_path(sticker_id: str) -> str:
    return StickerLibrary().resolve_path(sticker_id)


def resolve_sticker(sticker_id: str) -> dict | None:
    return StickerLibrary().resolve_item(sticker_id)


def fallback_sticker(query: str = "", *, min_score: float = 0.45) -> dict | None:
    return StickerLibrary().fallback_item(query, min_score=min_score)


def retire_sticker(sticker_id: str, *, reason: str = "", actor: str = "") -> dict | None:
    return StickerLibrary().retire_item(sticker_id, reason=reason, actor=actor)


def _looks_generic_sticker_save(decision: dict, understood: QQImageUnderstanding) -> bool:
    desc = str(decision.get("desc") or understood.description or "")
    reason = str(decision.get("reason") or "")
    usage = str(decision.get("usage_notes") or decision.get("use") or "")
    combined = f"{desc}\n{reason}\n{usage}"
    generic_hits = sum(
        phrase in combined
        for phrase in (
            "多种日常聊天场景",
            "高适用性",
            "缓解尴尬",
            "轻松幽默",
            "技术讨论",
            "有趣或略显复杂的问题",
        )
    )
    has_specific_text = bool(str(understood.text or "").strip()) and "无明显" not in str(understood.text)
    emotions = _string_list(decision.get("emotions"))
    scenes = _string_list(decision.get("scenes"))
    if generic_hits >= 3 and not has_specific_text:
        return True
    if len(set(emotions + scenes)) <= 2 and not has_specific_text:
        return True
    return False


def _looks_like_non_sticker_information_image(text: str) -> bool:
    markers = (
        "item tags",
        "curios:curio",
        "minecraft",
        "物品栏",
        "背包",
        "饰品",
        "栏位",
        "属性",
        "半径增加",
        "tooltip",
        "物品描述",
        "配方",
        "百科",
        "wiki",
        "设置页",
        "控制台",
        "报错",
        "stack trace",
    )
    return any(marker in text for marker in markers)


def _looks_like_low_information_sticker(text: str, image_text: str) -> bool:
    has_visible_text = bool(str(image_text or "").strip()) and "无明显" not in str(image_text)
    if has_visible_text:
        return False
    specific_markers = (
        "哭",
        "怒",
        "震惊",
        "疑惑",
        "崩溃",
        "害羞",
        "得意",
        "嫌弃",
        "猫",
        "狗",
        "角色",
        "人物",
        "梗",
        "reaction",
        "meme",
    )
    generic_markers = ("可爱", "微笑", "卡通", "简单", "普通", "轻松", "日常", "无明显")
    return any(marker in text for marker in generic_markers) and not any(marker in text for marker in specific_markers)
