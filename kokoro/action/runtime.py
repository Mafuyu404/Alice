"""Execution helpers for ActionBatch objects."""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Callable

from kokoro.action.model import Action, ActionBatch
from kokoro.core import input_events

logger = logging.getLogger(__name__)

ActionHandler = Callable[[Action], str]


class ActionRuntime:
    """Execute action batches and publish action lifecycle events."""

    def __init__(
        self,
        *,
        session,
        handlers: dict[str, ActionHandler],
        registry=None,
        tool_context: dict | None = None,
        merge_window_seconds: float = 1.0,
    ) -> None:
        self.session = session
        self.handlers = dict(handlers)
        self.registry = registry
        self.tool_context = dict(tool_context or {})
        self.merge_window_seconds = max(0.0, float(merge_window_seconds))
        self._pending: dict[str, list[input_events.InputEvent]] = defaultdict(list)
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def execute_batch(self, batch: ActionBatch) -> None:
        for action in batch.actions:
            self._publish_started(batch, action)
            if action.mode == "async":
                threading.Thread(target=self._execute_one, args=(batch, action), daemon=True).start()
            else:
                self._execute_one(batch, action)

    def _execute_one(self, batch: ActionBatch, action: Action) -> None:
        started = time.perf_counter()
        handler = self.handlers.get(action.action)
        if handler is None and self.registry is not None and hasattr(self.registry, "get_handler"):
            if self.registry.get_handler(action.action) is not None:
                handler = lambda current: self.registry.execute(current.action, dict(current.args), **self.tool_context)
        if handler is None:
            self._publish_result(
                batch,
                action,
                content=f"action is not registered: {action.action}",
                status="failed",
                elapsed=time.perf_counter() - started,
                priority="normal",
            )
            return
        try:
            content = handler(action)
            self._publish_result(
                batch,
                action,
                content=content or f"action completed: {action.action}",
                status="success",
                elapsed=time.perf_counter() - started,
                priority="normal",
            )
        except Exception as exc:
            logger.warning("action '%s' failed: %s", action.action, exc)
            self._publish_result(
                batch,
                action,
                content=f"action failed: {action.action}: {type(exc).__name__}: {exc}",
                status="failed",
                elapsed=time.perf_counter() - started,
                priority="normal",
            )

    def _publish_started(self, batch: ActionBatch, action: Action) -> None:
        if action.visibility != "public":
            return
        metadata = self._metadata(batch, action, status="started", elapsed=0.0)
        record = getattr(self.session, "record_self_action", None)
        if callable(record):
            reason = action.reason or batch.reason or "inner stream action selection"
            record(
                f"I started action: {action.action}. Reason: {reason}",
                source="action_runtime",
                action=action.action,
                metadata=metadata,
            )

    def _publish_result(
        self,
        batch: ActionBatch,
        action: Action,
        *,
        content: str,
        status: str,
        elapsed: float,
        priority: input_events.InputPriority,
    ) -> None:
        metadata = self._metadata(batch, action, status=status, elapsed=elapsed)
        event = input_events.build_action_result_event(
            content,
            source=action.action,
            metadata=metadata,
            priority=priority,
            lifetime="session",
        )
        if action.result_policy == "record_only":
            self._publish(event)
            return
        if priority == "urgent" or self.merge_window_seconds <= 0:
            self._publish(event)
            return
        self._enqueue_for_merge(batch.causality_id, event)

    def _enqueue_for_merge(self, key: str, event: input_events.InputEvent) -> None:
        with self._lock:
            self._pending[key].append(event)
            if key in self._timers:
                return
            timer = threading.Timer(self.merge_window_seconds, self._flush_key, args=(key,))
            timer.daemon = True
            self._timers[key] = timer
            timer.start()

    def _flush_key(self, key: str) -> None:
        with self._lock:
            events = self._pending.pop(key, [])
            self._timers.pop(key, None)
        if not events:
            return
        if len(events) == 1:
            self._publish(events[0])
            return
        first = events[0]
        lines = [event.visible_content() for event in events if event.visible_content()]
        metadata = dict(first.metadata)
        metadata["merged_count"] = len(events)
        metadata["merged_action_ids"] = [
            str(event.metadata.get("action_id") or "")
            for event in events
            if event.metadata.get("action_id")
        ]
        merged = input_events.build_action_result_event(
            "\n".join(lines),
            source="action_batch",
            metadata=metadata,
            priority=max((event.priority for event in events), key=_priority_rank),
            lifetime="session",
        )
        self._publish(merged)

    def _publish(self, event: input_events.InputEvent) -> None:
        bus = getattr(self.session, "event_bus", None)
        if bus is not None and hasattr(bus, "publish"):
            bus.publish(event)

    def _metadata(self, batch: ActionBatch, action: Action, *, status: str, elapsed: float) -> dict:
        return {
            "cycle_id": batch.cycle_id,
            "action_id": action.action_id,
            "causality_id": batch.causality_id,
            "action": action.action,
            "reason": action.reason or batch.reason,
            "status": status,
            "elapsed_seconds": round(max(0.0, elapsed), 3),
            "mode": action.mode,
            "visibility": action.visibility,
            "result_policy": action.result_policy,
        }


def _priority_rank(priority: str) -> int:
    return {"low": 0, "normal": 1, "high": 2, "urgent": 3}.get(priority, 1)
