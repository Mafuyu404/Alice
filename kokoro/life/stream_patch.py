"""Patch-first inner_stream updates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InnerStreamPatch:
    base_version: int = 0
    patches: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    full_text: str = ""

    @classmethod
    def from_raw(cls, raw: str | dict[str, Any]) -> "InnerStreamPatch":
        if isinstance(raw, str) and raw.strip() and not raw.strip().startswith("{"):
            return cls(
                patches=[{"op": "append", "text": raw.strip()}],
                reason="string patch normalized to append",
            )
        data = raw if isinstance(raw, dict) else _extract_json_object(str(raw or ""))
        if not isinstance(data, dict):
            raise ValueError("patch response is not a JSON object")
        patches = data.get("patches")
        if not isinstance(patches, list):
            patches = []
        return cls(
            base_version=int(data.get("base_version") or 0),
            patches=[item for item in patches if isinstance(item, dict)],
            reason=str(data.get("reason") or "").strip(),
            full_text=str(data.get("full_text") or "").strip(),
        )


@dataclass(frozen=True)
class PatchResult:
    text: str
    applied: bool
    reason: str = ""


def apply_inner_stream_patch(current: str, patch: InnerStreamPatch, *, max_chars: int = 1600) -> PatchResult:
    """Apply an LLM-produced textual patch without interpreting psychology."""

    text = str(current or "").strip()
    if patch.full_text:
        cleaned = _clean(patch.full_text, max_chars=max_chars)
        if _looks_like_meta_placeholder(cleaned):
            return PatchResult(text=text, applied=False, reason="full_text is meta placeholder")
        return PatchResult(text=cleaned, applied=True, reason="full_text")
    if not patch.patches:
        return PatchResult(text=text, applied=False, reason="empty patch")
    changed = False
    for item in patch.patches:
        op = str(item.get("op") or "").strip().lower()
        if op in {"append", "add"}:
            addition = str(item.get("text") or "").strip()
            if addition:
                if _already_present(text, addition):
                    continue
                text = (text + "\n" + addition).strip() if text else addition
                changed = True
            continue
        if op in {"replace", "replace_section"}:
            target = str(item.get("target") or item.get("section_hint") or "").strip()
            replacement = str(item.get("replacement") or item.get("text") or "").strip()
            if not replacement:
                continue
            if target and target in text:
                text = text.replace(target, replacement, 1)
                changed = True
            elif target:
                fuzzy = _find_fuzzy(text, target)
                if fuzzy:
                    start, end = fuzzy
                    text = text[:start] + replacement + text[end:]
                    changed = True
                else:
                    return PatchResult(text=text, applied=False, reason=f"target not found: {target[:60]}")
            else:
                text = replacement
                changed = True
            continue
        if op in {"prepend"}:
            addition = str(item.get("text") or "").strip()
            if addition:
                text = (addition + "\n" + text).strip() if text else addition
                changed = True
            continue
        return PatchResult(text=text, applied=False, reason=f"unsupported op: {op}")
    cleaned = _clean(text, max_chars=max_chars)
    if changed and _looks_like_meta_placeholder(cleaned):
        return PatchResult(text=str(current or "").strip(), applied=False, reason="patch text is meta placeholder")
    return PatchResult(text=cleaned, applied=changed, reason="applied" if changed else "no change")


def _already_present(text: str, addition: str) -> bool:
    normalized_addition = _normalize_for_dedupe(addition)
    if not normalized_addition:
        return True
    normalized_text = _normalize_for_dedupe(text)
    if normalized_addition in normalized_text:
        return True
    existing_parts = [
        _normalize_for_dedupe(part)
        for part in re.split(r"\n+|[。！？!?；;]", str(text or ""))
        if part.strip()
    ]
    return any(_similar_enough(part, normalized_addition) for part in existing_parts)


def _normalize_for_dedupe(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def _similar_enough(left: str, right: str) -> bool:
    if not left or not right:
        return False
    shorter = min(len(left), len(right))
    if shorter < 32:
        return False
    overlap = _longest_common_substring_len(left, right)
    if overlap / max(1, shorter) >= 0.88:
        return True
    bigram_containment = _ngram_containment(left, right, size=2)
    shared_long_chunks = len(_char_ngrams(left, size=4) & _char_ngrams(right, size=4))
    return bigram_containment >= 0.45 and shared_long_chunks >= 3


def _ngram_containment(left: str, right: str, *, size: int = 2) -> float:
    left_grams = _char_ngrams(left, size=size)
    right_grams = _char_ngrams(right, size=size)
    if not left_grams or not right_grams:
        return 0.0
    smaller = left_grams if len(left_grams) <= len(right_grams) else right_grams
    larger = right_grams if smaller is left_grams else left_grams
    return len(smaller & larger) / max(1, len(smaller))


def _char_ngrams(text: str, *, size: int) -> set[str]:
    compact = _normalize_for_dedupe(text)
    if len(compact) < size:
        return set()
    return {compact[index : index + size] for index in range(0, len(compact) - size + 1)}


def _longest_common_substring_len(left: str, right: str) -> int:
    previous = [0] * (len(right) + 1)
    best = 0
    for i, lch in enumerate(left, 1):
        current = [0] * (len(right) + 1)
        for j, rch in enumerate(right, 1):
            if lch == rch:
                current[j] = previous[j - 1] + 1
                if current[j] > best:
                    best = current[j]
        previous = current
    return best


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = re.sub(r"```(?:json)?\s*\n?(.*?)```", r"\1", raw, flags=re.DOTALL).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _find_fuzzy(text: str, target: str) -> tuple[int, int] | None:
    compact_target = re.sub(r"\s+", "", target)
    if len(compact_target) < 8:
        return None
    for match in re.finditer(r".{8,240}", text, flags=re.DOTALL):
        candidate = match.group(0)
        compact_candidate = re.sub(r"\s+", "", candidate)
        if compact_target in compact_candidate or compact_candidate in compact_target:
            return match.span()
    return None


def _clean(text: str, *, max_chars: int) -> str:
    text = re.sub(r"```(?:text|txt|markdown|plaintext)?\s*\n?(.*?)```", r"\1", str(text or "").strip(), flags=re.DOTALL)
    text = re.sub(r"^(?:text|txt|markdown|plaintext)\s*\n+", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    return text[-max(200, int(max_chars)) :].strip()


def _looks_like_meta_placeholder(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    lowered = compact.lower()
    if lowered in {"(empty)", "empty", "none", "null", "n/a", "无", "空", "暂无", "没有"}:
        return True
    bad_fragments = (
        "思考强度",
        "已更新inner_stream",
        "已更新内流",
        "状态：",
        "没有特别的想法",
        "没有特别想法",
        "没有特别的情绪",
        "没有特别情绪",
        "正在处理日常事务",
        "正在进行日常活动",
    )
    return any(fragment in compact for fragment in bad_fragments)
