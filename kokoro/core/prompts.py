"""Prompt template loading and formatting."""

from __future__ import annotations

import os
import tomllib
from typing import Any

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PROMPTS_DIR = os.path.join(_PROJECT_ROOT, "prompts")
_SKILLS_DIR = os.path.join(_PROMPTS_DIR, "skills")

_CACHE: dict[str, Any] | None = None
_SKILL_CACHE: dict[str, str] = {}


def load() -> dict[str, Any]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    merged: dict[str, Any] = {}
    if os.path.isdir(_PROMPTS_DIR):
        for filename in sorted(os.listdir(_PROMPTS_DIR)):
            if not filename.endswith(".toml"):
                continue
            path = os.path.join(_PROMPTS_DIR, filename)
            try:
                with open(path, "rb") as file:
                    data = tomllib.load(file)
            except Exception:
                continue
            if isinstance(data, dict):
                _deep_merge(merged, data)
    _CACHE = merged
    return _CACHE


def reload() -> dict[str, Any]:
    global _CACHE
    _CACHE = None
    _SKILL_CACHE.clear()
    return load()


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


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value
