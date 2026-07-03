"""Follow-up turn prediction for multi-character CLI runtime."""

from __future__ import annotations

import threading
from collections.abc import Callable


class MultiTurnPredictor:
    def __init__(
        self,
        *,
        orchestrator,
        enabled: Callable[[], bool],
        printer: Callable[[str], None] = print,
    ) -> None:
        self._orchestrator = orchestrator
        self._enabled = enabled
        self._printer = printer
        self._lock = threading.Lock()
        self._turn: object | None = None
        self._thread: threading.Thread | None = None
        self._serial = 0

    def clear(self) -> None:
        with self._lock:
            self._serial += 1
            self._turn = None
            self._thread = None

    def start(self, cid: str, cname: str, reply: str) -> None:
        if not self._enabled() or not reply:
            return
        with self._lock:
            self._serial += 1
            serial = self._serial

        def worker() -> None:
            try:
                prepared = self._orchestrator.prepare_followup_turn(cid, cname, reply)
            except Exception as exc:
                self._printer("  [multi-dialogue] prefetch failed: " + str(exc))
                prepared = None
            with self._lock:
                if serial == self._serial:
                    self._turn = prepared

        thread = threading.Thread(target=worker, daemon=True)
        with self._lock:
            self._turn = None
            self._thread = thread
        thread.start()

    def take(self, timeout: float = 0.1):
        thread = self._thread
        if thread:
            thread.join(timeout=max(0.0, timeout))
            if thread.is_alive():
                return "", "", ""
        with self._lock:
            prepared = self._turn
            self._turn = None
            self._thread = None
        return self._orchestrator.commit_prepared_turn(prepared)
