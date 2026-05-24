"""Inner-stream guided cognition reflection.

This bridges autonomous inputs such as QQ messages, image understanding,
search results, and self actions into the stable cognition layer.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from kokoro import config as cfg
from kokoro import input_events

logger = logging.getLogger(__name__)


class InnerCognitionReflection:
    def __init__(
        self,
        *,
        session,
        section: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        section = section or {}
        self.enabled = bool(section.get("enabled", True))
        self.consider_interval_seconds = max(0.0, float(section.get("consider_interval_seconds", 45.0) or 45.0))
        self.min_events = max(1, int(section.get("min_events", 1) or 1))
        self.max_event_chars = max(500, int(section.get("max_event_chars", 5000) or 5000))
        self._last_consider_at = 0.0
        self._lock = threading.Lock()

    def consider(
        self,
        *,
        inner_stream: str,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str = "",
    ) -> None:
        if not self.enabled or not events or not str(inner_stream or "").strip():
            return
        meaningful = [
            event for event in events
            if event.type != "time_tick"
            and event.source in {"qq", "qq_image", "web_search", "inner_memory", "qq_participation"}
            and event.metadata.get("conversation_id") != "private:self"
        ]
        if not any(event.source in {"qq", "qq_image"} for event in meaningful):
            return
        if not self._has_cognition_worthy_social_input(meaningful):
            return
        if len(meaningful) < self.min_events:
            return
        now = time.monotonic()
        if self.consider_interval_seconds > 0 and now - self._last_consider_at < self.consider_interval_seconds:
            return
        self._last_consider_at = now
        if not self._lock.acquire(blocking=False):
            return
        thread = threading.Thread(
            target=self._run,
            kwargs={
                "inner_stream": inner_stream,
                "events": meaningful,
                "context": dict(context or {}),
                "trigger_reason": trigger_reason,
            },
            daemon=True,
        )
        thread.start()

    def _run(
        self,
        *,
        inner_stream: str,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str,
    ) -> None:
        try:
            cognition = getattr(self.session, "cognition", None)
            if cognition is None or not hasattr(cognition, "evaluate_events"):
                return
            events_text = self._format_events(events)
            memory_context = context.get("memory_context") or ""
            if not memory_context:
                memory_context = self._lookup_memory(events_text)
            cognition.evaluate_events(
                events_text=events_text,
                inner_stream=inner_stream,
                summary=context.get("summary") or "",
                memories=memory_context or "",
                character_name=getattr(self.session, "character_name", ""),
                character_id=getattr(self.session, "character_id", ""),
                user_name=getattr(self.session, "user_name", "你"),
            )
        except Exception as exc:
            logger.debug("inner cognition reflection failed: %s", exc)
        finally:
            self._lock.release()

    def _lookup_memory(self, query: str) -> str:
        backend = getattr(self.session, "memory_backend", None)
        if backend is None or not hasattr(backend, "get_context_multi"):
            return ""
        try:
            from kokoro import memory as memory_mod

            return backend.get_context_multi(
                str(query or "")[:500],
                memory_mod.context_user_ids(
                    getattr(self.session, "character_id", ""),
                    getattr(self.session, "memory_counterpart", "") or getattr(self.session, "user_name", ""),
                ),
            ) or ""
        except Exception:
            return ""

    def _format_events(self, events: list[input_events.InputEvent]) -> str:
        lines: list[str] = []
        for event in events:
            meta = event.metadata or {}
            speaker = str(meta.get("speaker") or meta.get("sender") or "").strip()
            conversation_id = str(meta.get("conversation_id") or "").strip()
            prefix = []
            if conversation_id:
                prefix.append(f"conversation={conversation_id}")
            if speaker:
                prefix.append(f"speaker={speaker}")
            meta_text = f" ({', '.join(prefix)})" if prefix else ""
            lines.append(f"- type={event.type} source={event.source}{meta_text}: {event.visible_content()}")
        return "\n".join(lines)[: self.max_event_chars]

    def _has_cognition_worthy_social_input(self, events: list[input_events.InputEvent]) -> bool:
        qq_text_events = [
            event for event in events
            if event.source == "qq" and str(event.visible_content() or "").strip()
        ]
        if len(qq_text_events) >= 2:
            return True
        if any(_event_has_direct_or_named_social_signal(event) for event in qq_text_events):
            return True
        if any(event.source == "qq_participation" for event in events) and qq_text_events:
            return True
        # Image-only batches are useful as inner-stream input, but usually too weak
        # to create stable person-level cognition.
        return False


def _event_has_direct_or_named_social_signal(event: input_events.InputEvent) -> bool:
    text = str(event.visible_content() or "")
    meta = event.metadata or {}
    speaker = str(meta.get("speaker") or meta.get("sender") or "").strip()
    if speaker and speaker not in {"unknown", "有人"} and any(mark in text for mark in ("@", "雪吱", "小雪", "吱吱", "问", "回复")):
        return True
    return any(mark in text for mark in ("direct_to_self", "点名", "@了她", "接她话"))
