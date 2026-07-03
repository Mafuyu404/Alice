"""Primitive helpers for multi-character CLI speech runtime."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


def create_state_machine():
    from kokoro.core import state_machine as sm

    machine = sm.SystemStateMachine()
    machine.emit(sm.SystemEvent.INIT_DONE)
    return machine


def make_thread_safe_printer(printer=print) -> Callable[..., None]:
    lock = threading.Lock()

    def safe_print(*parts, sep=" ", end="\n") -> None:
        with lock:
            printer(*parts, sep=sep, end=end, flush=True)

    return safe_print


class SpeechGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._until = 0.0

    def hold(self, seconds: float) -> None:
        until = time.monotonic() + max(0.0, float(seconds))
        with self._lock:
            self._until = max(self._until, until)

    def blocked(self) -> bool:
        with self._lock:
            return time.monotonic() < self._until
