"""Memory/date event detector for proactive MEM desire."""

from __future__ import annotations

import datetime as _dt
import re
import time
from dataclasses import dataclass
from typing import Any

from kokoro import prompts


@dataclass(frozen=True)
class MemoryEvent:
    score: float
    context: str
    source: str
    event_id: str


@dataclass
class MemoryEventConfig:
    enabled: bool = False
    check_interval: float = 300.0
    cooldown_seconds: float = 21600.0
    date_score: float = 50.0
    memory_score: float = 70.0
    query: str = "recent important user preferences, plans, dates, anniversaries, goals"
    date_events: list[dict[str, str]] | None = None


class MemoryEventDetector:
    def __init__(
        self,
        memory_backend: object,
        user_id: str,
        config: MemoryEventConfig,
    ) -> None:
        self.memory_backend = memory_backend
        self.user_id = user_id
        self.config = config
        self._last_emit: dict[str, float] = {}

    def poll(self, now: _dt.date | None = None) -> list[MemoryEvent]:
        if not self.config.enabled:
            return []

        today = now or _dt.date.today()
        events: list[MemoryEvent] = []
        events.extend(self._date_events(today))
        memory_event = self._memory_lookup()
        if memory_event:
            events.append(memory_event)
        return [event for event in events if self._can_emit(event.event_id)]

    def mark_emitted(self, event: MemoryEvent) -> None:
        self._last_emit[event.event_id] = time.monotonic()

    def _date_events(self, today: _dt.date) -> list[MemoryEvent]:
        result: list[MemoryEvent] = []
        for index, item in enumerate(self.config.date_events or []):
            date_text = str(item.get("date", "")).strip()
            if not _date_matches(date_text, today):
                continue
            label = str(item.get("label", "special day")).strip()
            note = str(item.get("note", "")).strip()
            context = f"Today is {label}."
            if note:
                context += f" {note}"
            result.append(
                MemoryEvent(
                    score=self.config.date_score,
                    context=context,
                    source="date",
                    event_id=f"date:{date_text}:{index}",
                )
            )
        return result

    def _memory_lookup(self) -> MemoryEvent | None:
        if not getattr(self.memory_backend, "ready", False):
            return None
        query = self.config.query.strip()
        if not query:
            return None
        try:
            context = self.memory_backend.get_context(query, user_id=self.user_id)
        except Exception:
            return None
        context = _compact_context(context)
        if not context:
            return None
        return MemoryEvent(
            score=self.config.memory_score,
            context=prompts.format_prompt("memory_events.memory_lookup", context=context),
            source="memory",
            event_id=f"memory:{_stable_key(context)}",
        )

    def _can_emit(self, event_id: str) -> bool:
        last = self._last_emit.get(event_id, 0.0)
        return time.monotonic() - last >= self.config.cooldown_seconds


def from_config(config: dict, memory_backend: object, user_id: str) -> MemoryEventDetector:
    section = config.get("proactive", {})
    if not isinstance(section, dict):
        section = {}

    def number(key: str, default: float) -> float:
        try:
            return float(section.get(key, default))
        except (TypeError, ValueError):
            return default

    date_events = section.get("memory_date_events", [])
    if not isinstance(date_events, list):
        date_events = []

    event_config = MemoryEventConfig(
        enabled=bool(section.get("memory_events_enabled", False)),
        check_interval=max(30.0, number("memory_check_interval", 300.0)),
        cooldown_seconds=max(60.0, number("memory_cooldown_seconds", 21600.0)),
        date_score=max(0.0, number("memory_date_score", 50.0)),
        memory_score=max(0.0, number("memory_lookup_score", 70.0)),
        query=str(section.get("memory_lookup_query", MemoryEventConfig.query)),
        date_events=[item for item in date_events if isinstance(item, dict)],
    )
    return MemoryEventDetector(memory_backend, user_id, event_config)


def _date_matches(date_text: str, today: _dt.date) -> bool:
    if not date_text:
        return False
    for fmt in ("%Y-%m-%d", "%m-%d"):
        try:
            if fmt == "%Y-%m-%d":
                return _dt.date.fromisoformat(date_text) == today
            parsed = _dt.datetime.strptime(date_text, fmt)
            return parsed.month == today.month and parsed.day == today.day
        except ValueError:
            continue
    return False


def _compact_context(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    useful = [line for line in lines if not line.startswith("銆") and not line.startswith("【")]
    return "\n".join(useful[:4])[:700]


def _stable_key(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return str(abs(hash(normalized)) % 1_000_000_000)
