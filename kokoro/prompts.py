"""Prompt template loading and formatting."""

from __future__ import annotations

import json
import os
from typing import Any

_PROMPTS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts.json")

_CACHE: dict[str, Any] | None = None


def load() -> dict[str, Any]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    if os.path.exists(_PROMPTS_PATH):
        try:
            with open(_PROMPTS_PATH, "r", encoding="utf-8") as file:
                _CACHE = json.load(file)
        except Exception:
            _CACHE = {}
    else:
        _CACHE = {}
    return _CACHE


def get(path: str, default: Any = "") -> Any:
    current: Any = load()
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def format_prompt(path: str, **values: Any) -> str:
    template = get(path, "")
    if not isinstance(template, str):
        return ""
    return template.format(**values)
