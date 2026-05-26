"""Prompt template loading and formatting."""

from __future__ import annotations

import json
import os
from typing import Any

_PROMPTS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts.json")
_SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")

_CACHE: dict[str, Any] | None = None
_SKILL_CACHE: dict[str, str] = {}


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


def skill(name: str, default: str = "") -> str:
    safe_name = str(name or "").strip().replace("\\", "/").strip("/")
    if not safe_name or ".." in safe_name.split("/"):
        return default
    if safe_name in _SKILL_CACHE:
        return _SKILL_CACHE[safe_name]

    path = os.path.join(_SKILLS_DIR, f"{safe_name}.md")
    try:
        with open(path, "r", encoding="utf-8") as file:
            text = file.read().strip()
    except Exception:
        text = default
    _SKILL_CACHE[safe_name] = text
    return text
