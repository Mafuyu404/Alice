"""Prompt rendering context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PromptContext:
    scene: str
    character_id: str = ""
    character_name: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    debug_mode: bool = False
    trace_dir: str | None = None

    def get(self, key: str, default: Any = "") -> Any:
        return self.values.get(key, default)

    def require(self, key: str) -> Any:
        if key not in self.values:
            raise KeyError(f"missing prompt context value: {key}")
        return self.values[key]
