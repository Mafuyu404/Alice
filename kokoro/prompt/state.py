"""Prompt snapshot state used for diff-aware assembly."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PromptState:
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)

    def changed(self, fragment_id: str, snapshot: dict[str, Any] | None) -> bool:
        if snapshot is None:
            return True
        normalized = _normalize(snapshot)
        previous = self.snapshots.get(fragment_id)
        self.snapshots[fragment_id] = normalized
        return previous != normalized


def _normalize(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
