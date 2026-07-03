"""Helpers for filtering STT text that is probably TTS echo."""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from collections.abc import Callable

from kokoro.core import config as cfg


_ECHO_TEXT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_echo_text(text: str) -> str:
    return _ECHO_TEXT_RE.sub("", (text or "").lower())


def echo_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    max_len = min(len(a), len(b))
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            if k > best:
                best = k
    return best / max_len if max_len else 0.0


class TTSEchoFilter:
    def __init__(
        self,
        *,
        normalize: Callable[[str], str],
        similarity: Callable[[str, str], float],
        keep_seconds: Callable[[], float],
        min_chars: Callable[[], int],
        threshold: Callable[[], float],
        max_items: int = 12,
    ) -> None:
        self._normalize = normalize
        self._similarity = similarity
        self._keep_seconds = keep_seconds
        self._min_chars = min_chars
        self._threshold = threshold
        self._items: deque[tuple[float, str]] = deque(maxlen=max_items)
        self._lock = threading.Lock()

    def remember(self, text: str) -> None:
        norm = self._normalize(text)
        if not norm:
            return
        now = time.monotonic()
        keep_seconds = max(8.0, self._keep_seconds())
        with self._lock:
            self._items.append((now, norm))
            while self._items and now - self._items[0][0] > keep_seconds:
                self._items.popleft()

    def is_probable_echo(self, text: str) -> bool:
        norm = self._normalize(text)
        min_chars = max(2, self._min_chars())
        if len(norm) < min_chars:
            return False
        now = time.monotonic()
        keep_seconds = max(8.0, self._keep_seconds())
        threshold = max(0.5, min(0.98, self._threshold()))
        with self._lock:
            while self._items and now - self._items[0][0] > keep_seconds:
                self._items.popleft()
            for _, spoken in self._items:
                if len(norm) < 8:
                    if norm == spoken or norm in spoken or spoken.startswith(norm) or spoken.endswith(norm):
                        return True
                    continue
                if norm in spoken or spoken in norm:
                    return True
                if self._similarity(norm, spoken) >= threshold:
                    return True
                overlap = min(len(norm), len(spoken))
                if overlap >= 6 and (
                    norm[:overlap] == spoken[:overlap]
                    or norm[-overlap:] == spoken[-overlap:]
                ):
                    return True
        return False


def create_default_filter() -> TTSEchoFilter:
    return TTSEchoFilter(
        normalize=normalize_echo_text,
        similarity=echo_similarity,
        keep_seconds=cfg.stt_echo_filter_seconds,
        min_chars=cfg.stt_echo_filter_min_chars,
        threshold=cfg.stt_echo_filter_similarity,
    )
