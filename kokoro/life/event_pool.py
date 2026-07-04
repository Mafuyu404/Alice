"""High-throughput information pool for the life runtime."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from kokoro.core import input_events
from kokoro.core import lifecycle_debug


@dataclass(frozen=True)
class PooledEvent:
    event: input_events.InputEvent
    monotonic: float
    sequence: int


class InformationPool:
    """Bounded event pool used for fast batching, not hard classification."""

    def __init__(self, *, max_events: int = 512, clock=None) -> None:
        self.max_events = max(1, int(max_events))
        self.clock = clock
        self._events: deque[PooledEvent] = deque(maxlen=self.max_events)
        self._next_sequence = 1
        self._lock = threading.Lock()

    def add(self, event: input_events.InputEvent) -> PooledEvent:
        if not isinstance(event, input_events.InputEvent):
            raise TypeError("event must be InputEvent")
        now = self._now()
        with self._lock:
            pooled = PooledEvent(event=event, monotonic=now, sequence=self._next_sequence)
            self._next_sequence += 1
            self._events.append(pooled)
        lifecycle_debug.log("life.event_pool.add", event=event, sequence=pooled.sequence)
        return pooled

    def extend(self, events: Iterable[input_events.InputEvent]) -> list[PooledEvent]:
        return [self.add(event) for event in events]

    def snapshot(self, *, max_items: int | None = None) -> list[PooledEvent]:
        with self._lock:
            items = list(self._events)
        if max_items is None:
            return items
        return items[-max(0, int(max_items)) :]

    def batch_since(self, sequence: int, *, max_items: int | None = None) -> list[PooledEvent]:
        with self._lock:
            items = [item for item in self._events if item.sequence > sequence]
        if max_items is not None:
            items = items[-max(0, int(max_items)) :]
        return items

    def latest_sequence(self) -> int:
        with self._lock:
            if not self._events:
                return 0
            return self._events[-1].sequence

    def format_batch(self, items: Iterable[PooledEvent], *, max_chars: int = 4000) -> str:
        lines: list[str] = []
        for item in items:
            event = item.event
            content = event.visible_content()
            if not content:
                continue
            lines.append(
                f"[#{item.sequence} {event.timestamp} {event.type}/{event.source}] {content}"
            )
        text = "\n".join(lines)
        return text[-max(200, int(max_chars)) :]

    def _now(self) -> float:
        if self.clock is not None:
            return float(self.clock())
        import time

        return time.monotonic()
