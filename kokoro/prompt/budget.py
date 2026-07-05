"""Prompt budget helpers."""

from __future__ import annotations


def clamp_text(text: str, max_chars: int | None) -> tuple[str, bool]:
    if max_chars is None or max_chars < 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip(), True
