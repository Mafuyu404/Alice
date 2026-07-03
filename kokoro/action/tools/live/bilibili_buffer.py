"""Thread-safe Bilibili danmaku buffer."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


class DanmakuEntry:
    timestamp: float
    user: str
    text: str


class DanmakuBuffer:
    """Thread-safe bounded danmaku buffer with time-based expiry."""

    def __init__(self, max_age: float = 120.0):
        self._max_age = max_age
        self._entries: list[DanmakuEntry] = []
        self._lock = threading.Lock()

    def add(self, user: str, text: str) -> None:
        with self._lock:
            self._entries.append(DanmakuEntry(timestamp=time.time(), user=user, text=text))
            self._trim_locked()

    def drain(self) -> list[DanmakuEntry]:
        with self._lock:
            entries = list(self._entries)
            self._entries.clear()
            return entries

    def peek(self) -> list[DanmakuEntry]:
        with self._lock:
            self._trim_locked()
            return list(self._entries)

    def count(self) -> int:
        with self._lock:
            self._trim_locked()
            return len(self._entries)

    def _trim_locked(self) -> None:
        cutoff = time.time() - self._max_age
        self._entries = [e for e in self._entries if e.timestamp > cutoff]
