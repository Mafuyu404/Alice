"""Pending speech turn buffering helpers."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

from kokoro.core import config as cfg


def merge_text(prev: str, new: str) -> str:
    prev = (prev or "").strip()
    new = (new or "").strip()
    if not prev:
        return new
    if not new:
        return prev
    if new in prev:
        return prev
    if prev in new:
        return new
    max_overlap = min(len(prev), len(new))
    for n in range(max_overlap, 0, -1):
        if prev[-n:] == new[:n]:
            return prev + new[n:]
    return prev + new


def turn_deadline_delay(text: str) -> float:
    stripped = (text or "").strip()
    base = cfg.stt_turn_merge_seconds()
    if len(stripped) <= 4 and not re.search(r"[銆傦紒锛??鍚楀憿鍚у憖涔圿$", stripped):
        return max(base, 1.2)
    return base


@dataclass(frozen=True)
class PendingSpeechTurn:
    text: str
    reason: str
    deadline: float
    popped_at: float

    @property
    def merge_wait_seconds(self) -> float:
        return max(0.0, self.popped_at - (self.deadline or self.popped_at))


class PendingSpeechTurnBuffer:
    """Thread-safe text/deadline buffer for incremental STT turns."""

    def __init__(self, *, default_reason: str = "endpoint") -> None:
        self._default_reason = default_reason
        self._lock = threading.Lock()
        self._text = ""
        self._deadline = 0.0
        self._reason = default_reason

    def queue(self, text: str, *, reason: str | None = None, now: float | None = None) -> tuple[str, float]:
        now = time.monotonic() if now is None else now
        reason = str(reason or self._default_reason)
        with self._lock:
            self._text = merge_text(self._text, text)
            self._reason = reason
            delay = turn_deadline_delay(self._text)
            self._deadline = now + delay
            return self._text, delay

    def pop_ready(self, *, force: bool = False, now: float | None = None) -> PendingSpeechTurn | None:
        now = time.monotonic() if now is None else now
        with self._lock:
            deadline = float(self._deadline or 0.0)
            if not force and (not deadline or now < deadline):
                return None
            text = self._text.strip()
            reason = str(self._reason or self._default_reason)
            self._text = ""
            self._deadline = 0.0
            self._reason = self._default_reason
        if not text:
            return None
        return PendingSpeechTurn(text=text, reason=reason, deadline=deadline, popped_at=now)

    def prepend(self, text: str, *, now: float | None = None) -> tuple[str, float]:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._text = merge_text(text, self._text)
            delay = turn_deadline_delay(self._text)
            if not self._deadline:
                self._deadline = now + delay
            return self._text, delay

    def extend_or_replace(self, text: str, *, min_delay: float = 0.0, now: float | None = None) -> tuple[str, float]:
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._text:
                target = self._text
            else:
                self._text = str(text or "").strip()
                target = self._text
            delay = max(float(min_delay or 0.0), turn_deadline_delay(target))
            self._deadline = now + delay
            return target, delay

    def replace_if_empty(self, text: str, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._text:
                return False
            self._text = str(text or "").strip()
            self._deadline = now + turn_deadline_delay(self._text)
            return bool(self._text)
