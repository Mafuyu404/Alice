"""Compatibility facade for prompt template loading and formatting."""

from __future__ import annotations

from typing import Any

from kokoro.prompt import legacy


def load() -> dict[str, Any]:
    return legacy.load()


def reload() -> dict[str, Any]:
    return legacy.reload()


def get(path: str, default: Any = "") -> Any:
    return legacy.get(path, default)


def format_prompt(path: str, **values: Any) -> str:
    return legacy.format_prompt(path, **values)


def skill(name: str, default: str = "") -> str:
    return legacy.skill(name, default)
