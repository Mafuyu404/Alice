"""Append-only raw experience log."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from kokoro.memory.models import MemoryEventDraft, new_id


class MemoryEventLog:
    def __init__(self, *, character_id: str, root: Path) -> None:
        self.character_id = character_id
        self.root = Path(root)
        self.path = self.root / "characters" / character_id / "memory" / "events"
        self.path.mkdir(parents=True, exist_ok=True)

    def append(self, event: MemoryEventDraft) -> str:
        if not str(event.content or "").strip():
            return ""
        day = _day_from_timestamp(event.timestamp)
        path = self.path / f"{day}.jsonl"
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event.as_json(), ensure_ascii=False, default=str) + "\n")
        return event.event_id

    def append_input_event(self, event: object, *, memory_policy: str | None = None) -> str:
        metadata = dict(getattr(event, "metadata", {}) or {})
        policy = memory_policy or str(metadata.get("memory_policy") or "")
        if not policy:
            policy = _policy_for_event(event)
        draft = MemoryEventDraft(
            character_id=self.character_id,
            event_id=str(getattr(event, "id", "") or "") or new_id("evt"),
            timestamp=str(getattr(event, "timestamp", "") or ""),
            source=str(getattr(event, "source", "") or "unknown"),
            event_type=str(getattr(event, "type", "") or "text"),
            content=str(getattr(event, "content", "") or ""),
            memory_policy=policy if policy in {"experience", "control", "debug", "ephemeral", "blocked"} else "experience",
            tool_name=str(metadata.get("action") or metadata.get("tool_name") or ""),
            links={k: v for k, v in metadata.items() if k in {"action_id", "inner_stream_version", "open_thread_id"}},
            metadata=metadata,
        )
        return self.append(draft)

    def recent_events(self, *, limit: int = 40) -> list[dict[str, Any]]:
        files = sorted(self.path.glob("*.jsonl"), reverse=True)
        result: list[dict[str, Any]] = []
        for file_path in files:
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                result.append(item)
                if len(result) >= limit:
                    return list(reversed(result))
        return list(reversed(result))


def _day_from_timestamp(timestamp: str) -> str:
    try:
        return datetime.fromisoformat(str(timestamp or "")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _policy_for_event(event: object) -> str:
    source = str(getattr(event, "source", "") or "").lower()
    event_type = str(getattr(event, "type", "") or "").lower()
    lifetime = str(getattr(event, "lifetime", "") or "").lower()
    privacy = getattr(event, "privacy", None)
    if getattr(privacy, "private", False):
        return "blocked"
    if source in {"system", "system_clock"} or event_type == "time_tick":
        return "ephemeral"
    if "debug" in source:
        return "debug"
    if lifetime == "ephemeral":
        return "ephemeral"
    return "experience"
