"""Execution helpers for ActionBatch objects."""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Callable

from kokoro.action.model import Action, ActionBatch
from kokoro.action import tool_spec
from kokoro.core import input_events
from kokoro.core import lifecycle_debug

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
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def execute_batch(self, batch: ActionBatch) -> None:
        lifecycle_debug.log("action_runtime.batch.start", batch=batch)
        for action in batch.actions:
            self._publish_started(batch, action)
            if action.mode == "async":
                lifecycle_debug.log("action_runtime.action.thread_start", batch=batch, action=action)
                thread = threading.Thread(target=self._execute_one, args=(batch, action), daemon=True)
                with self._lock:
                    self._threads.append(thread)
                thread.start()
            else:
                self._execute_one(batch, action)

    def execute_action_for_result(self, batch: ActionBatch, action: Action) -> str:
        """Execute one synchronous action and return the handler result.

        Compatibility path for old call sites that must feed the tool result
        back into an LLM turn while still publishing action lifecycle events.
        """
        self._publish_started(batch, action)
        lifecycle_debug.log("action_runtime.compat_execute.start", batch=batch, action=action)
        return self._execute_one(batch, action)

    def _execute_one(self, batch: ActionBatch, action: Action) -> str:
        started = time.perf_counter()
        lifecycle_debug.log("action_runtime.action.start", batch=batch, action=action)
        handler = self.handlers.get(action.action)
        if handler is None and self.registry is not None and hasattr(self.registry, "resolve"):
            result = self._execute_tool_lifecycle(action)
            self._publish_result(
                batch,
                action,
                content=result.content or f"action completed: {action.action}",
                status=result.status or "success",
                elapsed=time.perf_counter() - started,
                priority=_normalize_priority(result.priority),
                extra_metadata=result.metadata,
            )
            lifecycle_debug.log("action_runtime.action.done", batch=batch, action=action, result=result)
            return result.content or f"action completed: {action.action}"
        if handler is None and self.registry is not None and hasattr(self.registry, "get_handler"):
            if self.registry.get_handler(action.action) is not None:
                handler = lambda current: self.registry.execute(current.action, dict(current.args), **self.tool_context)
        if handler is None:
            lifecycle_debug.log("action_runtime.action.unregistered", batch=batch, action=action)
            self._publish_result(
                batch,
                action,
                content=f"action is not registered: {action.action}",
                status="failed",
                elapsed=time.perf_counter() - started,
                priority="normal",
                extra_metadata={},
            )
            return f"action is not registered: {action.action}"
        try:
            content = handler(action)
            lifecycle_debug.log("action_runtime.handler.done", batch=batch, action=action, content=content)
            self._publish_result(
                batch,
                action,
                content=content or f"action completed: {action.action}",
                status="success",
                elapsed=time.perf_counter() - started,
                priority="normal",
                extra_metadata={},
            )
            return content or f"action completed: {action.action}"
        except Exception as exc:
            logger.warning("action '%s' failed: %s", action.action, exc)
            content = f"action failed: {action.action}: {type(exc).__name__}: {exc}"
            lifecycle_debug.log(
                "action_runtime.handler.error",
                batch=batch,
                action=action,
                error=str(exc),
                content=content,
            )
            self._publish_result(
                batch,
                action,
                content=content,
                status="failed",
                elapsed=time.perf_counter() - started,
                priority="normal",
                extra_metadata={},
            )
            return content

    def _execute_tool_lifecycle(self, action: Action) -> tool_spec.ToolResult:
        ctx = tool_spec.ToolContext(
            session=self.session,
            data={
                **self.tool_context,
                "tool_timeout": self.tool_context.get("tool_timeout"),
            },
        )
        lifecycle_debug.log("action_runtime.tool_lifecycle.start", action=action, context=ctx.data)
        return self.registry.execute(ctx, action)

    def _publish_started(self, batch: ActionBatch, action: Action) -> None:
        if action.visibility != "public":
            lifecycle_debug.log("action_runtime.started.private_skip", batch=batch, action=action)
            return
        metadata = self._metadata(batch, action, status="started", elapsed=0.0)
        lifecycle_debug.log("action_runtime.started.publish", batch=batch, action=action, metadata=metadata)
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
        extra_metadata: dict | None = None,
    ) -> None:
        metadata = self._metadata(batch, action, status=status, elapsed=elapsed)
        metadata.update(extra_metadata or {})
        if action.args.get("suppress_feedback"):
            metadata["suppress_feedback"] = True
        lifecycle_debug.log(
            "action_runtime.result.build",
            batch=batch,
            action=action,
            content=content,
            status=status,
            elapsed=elapsed,
            priority=priority,
            metadata=metadata,
        )
        event = input_events.build_action_result_event(
            content,
            source=action.action,
            metadata=metadata,
            priority=priority,
            lifetime="session",
        )
        if action.result_policy == "record_only":
            lifecycle_debug.log("action_runtime.result.publish_record_only", event=event)
            if metadata.get("suppress_feedback"):
                lifecycle_debug.log("action_runtime.result.suppressed", event=event)
                return
            self._publish(event)
            return
        if priority == "urgent" or self.merge_window_seconds <= 0:
            lifecycle_debug.log("action_runtime.result.publish_immediate", event=event)
            self._publish(event)
            return
        self._enqueue_for_merge(batch.causality_id, event)

    def _enqueue_for_merge(self, key: str, event: input_events.InputEvent) -> None:
        with self._lock:
            self._pending[key].append(event)
            if key in self._timers:
                lifecycle_debug.log(
                    "action_runtime.result.merge_append",
                    causality_id=key,
                    pending_count=len(self._pending[key]),
                    event=event,
                )
                return
            timer = threading.Timer(self.merge_window_seconds, self._flush_key, args=(key,))
            timer.daemon = True
            self._timers[key] = timer
            timer.start()
            lifecycle_debug.log(
                "action_runtime.result.merge_timer_start",
                causality_id=key,
                pending_count=len(self._pending[key]),
                merge_window_seconds=self.merge_window_seconds,
                event=event,
            )

    def _flush_key(self, key: str) -> None:
        with self._lock:
            events = self._pending.pop(key, [])
            self._timers.pop(key, None)
        if not events:
            return
        if len(events) == 1:
            lifecycle_debug.log("action_runtime.result.merge_flush_single", causality_id=key, event=events[0])
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
        lifecycle_debug.log("action_runtime.result.merge_flush_many", causality_id=key, events=events, merged=merged)
        self._publish(merged)

    def flush_pending(self) -> None:
        with self._lock:
            keys = list(self._pending)
            timers = list(self._timers.values())
        for timer in timers:
            try:
                timer.cancel()
            except Exception:
                pass
        lifecycle_debug.log("action_runtime.result.flush_pending", keys=keys)
        for key in keys:
            self._flush_key(key)

    def _publish(self, event: input_events.InputEvent) -> None:
        bus = getattr(self.session, "event_bus", None)
        if bus is not None and hasattr(bus, "publish"):
            lifecycle_debug.log("action_runtime.event_publish", event=event)
            bus.publish(event)
        else:
            lifecycle_debug.log("action_runtime.event_publish.no_bus", event=event)

    def shutdown(self, *, wait: bool = True, timeout: float = 5.0) -> None:
        lifecycle_debug.log("action_runtime.shutdown.start", wait=wait, timeout=timeout)
        self.flush_pending()
        with self._lock:
            timers = list(self._timers.values())
            threads = list(self._threads)
        for timer in timers:
            try:
                timer.cancel()
            except Exception:
                pass
        if wait:
            deadline = time.monotonic() + max(0.0, timeout)
            for thread in threads:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining <= 0:
                    break
                if thread.is_alive():
                    thread.join(timeout=remaining)
        if self.registry is not None and hasattr(self.registry, "shutdown"):
            self.registry.shutdown()
        lifecycle_debug.log("action_runtime.shutdown.done")

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


def _normalize_priority(priority: object) -> input_events.InputPriority:
    text = str(priority or "").strip().lower()
    if text in ("low", "normal", "high", "urgent"):
        return text  # type: ignore[return-value]
    return "normal"
