"""Inner narrative stream for self-expression.

The stream is intentionally plain text.  Runtime code may read, write, and
inject it, but must not parse it into rules or scores.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from kokoro.core import input_events
from kokoro.core import lifecycle_debug

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CHARACTERS_DIR = str(_PROJECT_ROOT / "characters")
_LOGS_DIR = str(_PROJECT_ROOT / "logs")


class InnerStream:
    """A character's current inner continuity, maintained by an LLM."""

    def __init__(
        self,
        character_id: str,
        character_data: dict | None = None,
        *,
        reset_on_start: bool = False,
    ):
        self.character_id = character_id
        self.character_data = character_data or {}
        self._path = os.path.join(_CHARACTERS_DIR, character_id, "inner_stream.txt")
        lifecycle_debug.log(
            "inner_stream.init",
            character_id=character_id,
            path=self._path,
            reset_on_start=reset_on_start,
            has_character_data=bool(character_data),
        )
        self.text: str = ""
        self._last_event_digest: str = ""
        if reset_on_start:
            self.text = ""
            self._save()
        else:
            self._load()

    def get_context(self) -> str:
        if not self.text.strip():
            return ""
        return "【内在叙事流】\n" + self.text.strip()

    def evaluate(
        self,
        *,
        user_text: str,
        assistant_text: str,
        character_name: str,
        user_name: str,
        summary: str = "",
        recent_history: str = "",
        cognition_context: str = "",
        emotion_context: str = "",
        memory_context: str = "",
        scene_context: str = "",
    ) -> dict:
        """Rewrite the stream after a meaningful turn.

        This returns debug data for tests/tools.  Failures are non-fatal.
        """
        from kokoro.core import config as cfg
        from kokoro.core import prompts
        from kokoro.core import token_usage

        debug = {
            "system_prompt": "",
            "user_prompt": "",
            "raw_response": "",
            "before": self.text,
            "after": self.text,
            "saved": False,
            "error": "",
        }

        section = cfg.inner_stream_config()
        if not section.get("enabled", True):
            lifecycle_debug.log("inner_stream.evaluate.disabled", character_id=self.character_id)
            return debug

        system_prompt = prompts.format_prompt(
            "inner_stream.evaluate_system",
            name=character_name,
            user_name=user_name,
        )
        profile = _compact_profile(self.character_data)
        user_prompt = prompts.format_prompt(
            "inner_stream.evaluate_user",
            name=character_name,
            user_name=user_name,
            current=self.text.strip() or "（空）",
            profile=profile or "（无）",
            summary=summary or "（无）",
            recent_history=recent_history or "（无）",
            cognition_context=cognition_context or "（无）",
            emotion_context=emotion_context or "（无）",
            memory_context=memory_context or "（无）",
            scene_context=scene_context or "（无）",
            user_text=user_text or "（无）",
            assistant_text=assistant_text or "（无）",
        )
        debug["system_prompt"] = system_prompt
        debug["user_prompt"] = user_prompt

        model = str(section.get("model") or "").strip() or cfg.llm_model()
        max_tokens = int(section.get("max_tokens", 350) or 350)
        try:
            from kokoro.core import deepseek_api

            lifecycle_debug.log(
                "inner_stream.evaluate.llm.start",
                character_id=self.character_id,
                model=model,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                before=self.text,
            )
            result = deepseek_api.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=model,
                temperature=0.5,
                max_tokens=max_tokens,
                function="inner_stream_evaluate",
            )
            text = result["content"]

            debug["raw_response"] = text
            lifecycle_debug.log(
                "inner_stream.evaluate.llm.result",
                character_id=self.character_id,
                raw_response=text,
            )
            cleaned = _clean_stream_text(text, max_chars=int(section.get("max_chars", 1200) or 1200))
            if cleaned and _looks_complete(cleaned):
                self.text = cleaned
                self._save()
                debug["after"] = self.text
                debug["saved"] = True
            lifecycle_debug.log(
                "inner_stream.evaluate.done",
                character_id=self.character_id,
                saved=debug["saved"],
                error=debug["error"],
                after=debug["after"],
            )
        except Exception as exc:
            debug["error"] = str(exc)
            logger.debug("inner stream evaluation failed: %s", exc)
            lifecycle_debug.log(
                "inner_stream.evaluate.error",
                character_id=self.character_id,
                error=str(exc),
            )
        return debug

    def evaluate_events(
        self,
        *,
        events: list[input_events.InputEvent],
        character_name: str,
        user_name: str,
        summary: str = "",
        recent_history: str = "",
        cognition_context: str = "",
        emotion_context: str = "",
        memory_context: str = "",
        scene_context: str = "",
        activity_context: str = "",
        trigger_reason: str = "",
    ) -> dict:
        """Rewrite the stream from a batch of unified runtime events."""
        from kokoro.core import config as cfg
        from kokoro.core import prompts
        from kokoro.core import token_usage

        debug = {
            "system_prompt": "",
            "user_prompt": "",
            "raw_response": "",
            "before": self.text,
            "after": self.text,
            "saved": False,
            "error": "",
            "skipped": "",
        }

        section = cfg.inner_stream_config()
        if not section.get("enabled", True) or not events:
            lifecycle_debug.log(
                "inner_stream.events.disabled_or_empty",
                character_id=self.character_id,
                enabled=section.get("enabled", True),
                event_count=len(events or []),
            )
            return debug

        # Skip guard: if all meaningful events are identical to the previous batch
        # and the stream already has content, don't waste an LLM call.
        has_meaningful = any(
            (
                event.type == "time_tick"
                and bool((event.metadata or {}).get("time_signal"))
            )
            or (
                event.type != "time_tick"
                and _event_type_has_content(event)
            )
            for event in events
        )
        if not has_meaningful and self.text.strip():
            debug["skipped"] = "all events are ambient ticks, skipping"
            lifecycle_debug.log(
                "inner_stream.events.skip",
                character_id=self.character_id,
                skipped=debug["skipped"],
                events=events,
                trigger_reason=trigger_reason,
            )
            return debug
        digest = _events_digest(events)
        if digest == self._last_event_digest and self.text.strip():
            debug["skipped"] = "events unchanged since last update, skipping"
            lifecycle_debug.log(
                "inner_stream.events.skip",
                character_id=self.character_id,
                skipped=debug["skipped"],
                digest=digest,
                events=events,
                trigger_reason=trigger_reason,
            )
            return debug
        self._last_event_digest = digest

        system_prompt = prompts.format_prompt(
            "inner_stream.events_system",
            name=character_name,
            user_name=user_name,
            inner_continuity_skill=prompts.skill("inner_continuity"),
            social_presence_skill=prompts.skill("social_presence"),
            memory_cognition_skill=prompts.skill("memory_cognition"),
        )
        profile = _compact_profile(self.character_data)
        user_prompt = prompts.format_prompt(
            "inner_stream.events_user",
            name=character_name,
            user_name=user_name,
            current=self.text.strip() or "（空）",
            profile=profile or "（无）",
            summary=summary or "（无）",
            recent_history=recent_history or "（无）",
            cognition_context=cognition_context or "（无）",
            emotion_context=emotion_context or "（无）",
            memory_context=memory_context or "（无）",
            scene_context=scene_context or "（无）",
            activity_context=activity_context or "（无）",
            events=input_events.format_events_for_prompt(
                events,
                max_chars=_event_prompt_max_chars(section, events, trigger_reason),
            ) or "（无）",
            trigger_reason=trigger_reason or "事件短窗口合并",
        )
        debug["system_prompt"] = system_prompt
        debug["user_prompt"] = user_prompt

        model = str(section.get("model") or "").strip() or cfg.llm_model()
        max_tokens = int(section.get("max_tokens", 350) or 350)
        try:
            from kokoro.core import deepseek_api

            lifecycle_debug.log(
                "inner_stream.events.llm.start",
                character_id=self.character_id,
                model=model,
                max_tokens=max_tokens,
                trigger_reason=trigger_reason,
                events=events,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                before=self.text,
            )
            result = deepseek_api.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=model,
                temperature=0.5,
                max_tokens=max_tokens,
                function="inner_stream_events",
            )
            text = result["content"]

            debug["raw_response"] = text
            lifecycle_debug.log(
                "inner_stream.events.llm.result",
                character_id=self.character_id,
                trigger_reason=trigger_reason,
                raw_response=text,
            )
            cleaned = _clean_stream_text(text, max_chars=int(section.get("max_chars", 1200) or 1200))
            if cleaned and _looks_complete(cleaned):
                self.text = cleaned
                self._save()
                debug["after"] = self.text
                debug["saved"] = True
            lifecycle_debug.log(
                "inner_stream.events.done",
                character_id=self.character_id,
                trigger_reason=trigger_reason,
                saved=debug["saved"],
                error=debug["error"],
                before=debug["before"],
                after=debug["after"],
            )
            _log_inner_stream_update(
                self.character_id,
                enabled=bool(section.get("log_updates", True)),
                trigger_reason=trigger_reason,
                events=events,
                before=debug["before"],
                after=debug["after"],
                raw=text,
                saved=debug["saved"],
                error=debug["error"],
            )
        except Exception as exc:
            debug["error"] = str(exc)
            logger.debug("inner stream event evaluation failed: %s", exc)
            lifecycle_debug.log(
                "inner_stream.events.error",
                character_id=self.character_id,
                trigger_reason=trigger_reason,
                events=events,
                error=str(exc),
                raw_response=debug["raw_response"],
            )
            _log_inner_stream_update(
                self.character_id,
                enabled=bool(section.get("log_updates", True)),
                trigger_reason=trigger_reason,
                events=events,
                before=debug["before"],
                after=debug["after"],
                raw=debug["raw_response"],
                saved=debug["saved"],
                error=debug["error"],
            )
        return debug

    def _load(self) -> None:
        if not os.path.exists(self._path):
            self.text = ""
            lifecycle_debug.log("inner_stream.load.missing", character_id=self.character_id, path=self._path)
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self.text = _clean_stream_text(f.read(), max_chars=1600)
            lifecycle_debug.log(
                "inner_stream.load.done",
                character_id=self.character_id,
                path=self._path,
                text=self.text,
            )
        except Exception as exc:
            logger.warning("failed to load inner stream: %s", exc)
            self.text = ""
            lifecycle_debug.log(
                "inner_stream.load.error",
                character_id=self.character_id,
                path=self._path,
                error=str(exc),
            )

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                f.write(self.text.strip() + "\n")
            lifecycle_debug.log(
                "inner_stream.save.done",
                character_id=self.character_id,
                path=self._path,
                text=self.text,
            )
        except Exception as exc:
            logger.warning("failed to save inner stream: %s", exc)
            lifecycle_debug.log(
                "inner_stream.save.error",
                character_id=self.character_id,
                path=self._path,
                error=str(exc),
            )


def _clean_stream_text(text: str, *, max_chars: int) -> str:
    text = re.sub(r"```(?:text|markdown)?\s*\n?(.*?)```", r"\1", str(text), flags=re.DOTALL)
    text = text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:max(200, max_chars)].strip()


def _looks_complete(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 40:
        return False
    if stripped.endswith(("这", "那", "但", "而", "因为", "所以", "如果", "不是", "可以", "一个")):
        return False
    return True


def _compact_profile(data: dict) -> str:
    parts: list[str] = []
    for key in ("name", "description", "personality", "background", "relationship"):
        value = str(data.get(key, "") or "").strip()
        if value:
            parts.append(f"{key}: {value[:500]}")
    return "\n\n".join(parts)


class InnerStreamLoop:
    """Rhythm-driven inner narrative updater.

    Events wake the loop and can pull the next update closer, but the loop
    updates at its own cadence and reads the queued events only when due.
    """

    def __init__(
        self,
        *,
        stream: InnerStream,
        context_provider,
        event_delay_seconds: float = 2.0,
        idle_interval_seconds: float = 240.0,
        time_tick_interval_seconds: float = 900.0,
        max_batch: int = 16,
        search_impulse=None,
        output_handlers: list | None = None,
    ) -> None:
        self.stream = stream
        self.context_provider = context_provider
        self.event_delay_seconds = max(0.0, float(event_delay_seconds))
        self.idle_interval_seconds = max(0.5, float(idle_interval_seconds))
        self.time_tick_interval_seconds = max(0.0, float(time_tick_interval_seconds))
        self.max_batch = max(1, int(max_batch))
        self.search_impulse = search_impulse
        self.output_handlers = list(output_handlers or [])
        self._events: list[input_events.InputEvent] = []
        self._lock = threading.Lock()
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        now = time.monotonic()
        self._next_due = now + self.idle_interval_seconds
        self._last_update = 0.0
        self._last_time_tick = now

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        lifecycle_debug.log(
            "inner_stream_loop.start",
            character_id=self.stream.character_id,
            event_delay_seconds=self.event_delay_seconds,
            idle_interval_seconds=self.idle_interval_seconds,
            time_tick_interval_seconds=self.time_tick_interval_seconds,
            max_batch=self.max_batch,
            output_handlers=[getattr(h, "__qualname__", repr(h)) for h in self.output_handlers],
        )

    def stop(self, *, flush: bool = True) -> None:
        lifecycle_debug.log("inner_stream_loop.stop", character_id=self.stream.character_id, flush=flush)
        if flush:
            self.flush()
        self._stop.set()
        self._wakeup.set()

    def submit(self, event: input_events.InputEvent) -> None:
        with self._lock:
            self._events.append(event)
            self._schedule_for_event_locked(event)
            pending = len(self._events)
            next_due = self._next_due
        lifecycle_debug.log(
            "inner_stream_loop.submit",
            character_id=self.stream.character_id,
            event=event,
            pending_count=pending,
            next_due_monotonic=round(next_due, 6),
        )
        self._wakeup.set()

    def flush(self) -> None:
        events = self._pop_events()
        if not events:
            events = [self._time_tick_event(reason="manual flush")]
        lifecycle_debug.log(
            "inner_stream_loop.flush",
            character_id=self.stream.character_id,
            events=events,
        )
        self._evaluate(events, trigger_reason="manual flush")

    def evaluate_now(
        self,
        events: list[input_events.InputEvent] | None = None,
        *,
        trigger_reason: str = "manual evaluate",
    ) -> None:
        if events is None:
            events = self._pop_events()
        if not events:
            return
        self._evaluate(events, trigger_reason=trigger_reason)

    def _run(self) -> None:
        while not self._stop.is_set():
            timeout = self._seconds_until_due()
            self._wakeup.wait(timeout=timeout)
            self._wakeup.clear()
            if self._stop.is_set():
                break
            if not self._is_due():
                continue
            events = self._pop_events(limit=self.max_batch)
            reason = "节奏更新"
            if not events:
                events = [self._time_tick_event(reason="idle_heartbeat")]
                reason = "内在心跳"
            elif events:
                reason = "事件唤醒后的节奏更新"
            if events:
                lifecycle_debug.log(
                    "inner_stream_loop.due",
                    character_id=self.stream.character_id,
                    reason=reason,
                    events=events,
                    pending_after_pop=self.pending_count(),
                )
                self._evaluate(events, trigger_reason=reason)
            self._schedule_next_idle()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._events)

    def _pop_events(self, limit: int | None = None) -> list[input_events.InputEvent]:
        with self._lock:
            if not self._events:
                return []
            if limit is None or len(self._events) <= limit:
                events = self._events
                self._events = []
                return events
            events = self._events[:limit]
            self._events = self._events[limit:]
            if self._events:
                self._schedule_soon_locked()
                self._wakeup.set()
            return events

    def _evaluate(self, events: list[input_events.InputEvent], *, trigger_reason: str) -> None:
        try:
            lifecycle_debug.log(
                "inner_stream_loop.evaluate.start",
                character_id=self.stream.character_id,
                trigger_reason=trigger_reason,
                events=events,
            )
            context = self.context_provider() or {}
            lifecycle_debug.log(
                "inner_stream_loop.context",
                character_id=self.stream.character_id,
                trigger_reason=trigger_reason,
                context=context,
            )
            result = self.stream.evaluate_events(events=events, trigger_reason=trigger_reason, **context)
            self._last_update = time.monotonic()
            lifecycle_debug.log(
                "inner_stream_loop.evaluate.result",
                character_id=self.stream.character_id,
                trigger_reason=trigger_reason,
                result=result,
            )
            if result.get("skipped"):
                return  # nothing changed, don't fire output handlers
            if self.search_impulse is not None and hasattr(self.search_impulse, "consider"):
                search_context = dict(context)
                search_context["event_sources"] = ",".join(event.source for event in events)
                search_context["event_types"] = ",".join(event.type for event in events)
                self.search_impulse.consider(
                    inner_stream=self.stream.text,
                    context=search_context,
                )
            for handler in self.output_handlers:
                try:
                    lifecycle_debug.log(
                        "inner_stream_loop.output_handler.start",
                        character_id=self.stream.character_id,
                        handler=getattr(handler, "__qualname__", repr(handler)),
                        trigger_reason=trigger_reason,
                        events=events,
                    )
                    handler(
                        inner_stream=self.stream.text,
                        events=events,
                        context=context,
                        trigger_reason=trigger_reason,
                    )
                    lifecycle_debug.log(
                        "inner_stream_loop.output_handler.done",
                        character_id=self.stream.character_id,
                        handler=getattr(handler, "__qualname__", repr(handler)),
                    )
                except Exception as exc:
                    logger.debug("inner stream output handler failed: %s", exc)
                    lifecycle_debug.log(
                        "inner_stream_loop.output_handler.error",
                        character_id=self.stream.character_id,
                        handler=getattr(handler, "__qualname__", repr(handler)),
                        error=str(exc),
                    )
        except Exception as exc:
            logger.warning("inner stream loop failed: %s", exc)
            lifecycle_debug.log(
                "inner_stream_loop.evaluate.error",
                character_id=self.stream.character_id,
                trigger_reason=trigger_reason,
                error=str(exc),
            )

    def _schedule_for_event_locked(self, event: input_events.InputEvent) -> None:
        now = time.monotonic()
        delay = self.event_delay_seconds
        if event.priority == "urgent":
            delay = min(delay, 0.5)
        elif event.priority == "high":
            delay = min(delay, 1.0)
        elif event.priority == "low":
            delay = max(delay, min(30.0, self.idle_interval_seconds))
        self._next_due = min(self._next_due, now + delay)

    def _schedule_soon_locked(self) -> None:
        self._next_due = min(self._next_due, time.monotonic() + self.event_delay_seconds)

    def _schedule_next_idle(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._next_due = now + self.idle_interval_seconds
            if self._events:
                self._schedule_soon_locked()

    def _seconds_until_due(self) -> float:
        with self._lock:
            due = self._next_due
        return max(0.1, min(60.0, due - time.monotonic()))

    def _is_due(self) -> bool:
        with self._lock:
            return time.monotonic() >= self._next_due

    def _should_time_tick(self) -> bool:
        if self.time_tick_interval_seconds <= 0:
            return False
        return time.monotonic() - self._last_time_tick >= self.time_tick_interval_seconds

    def _time_tick_event(self, *, reason: str) -> input_events.InputEvent:
        now = time.monotonic()
        elapsed = now - (self._last_update or self._last_time_tick)
        include_time_signal = self._should_time_tick()
        if include_time_signal:
            self._last_time_tick = now
        return input_events.build_time_tick_event(
            (
                f"距离上次内在叙事流更新约 {elapsed:.0f} 秒，期间没有更强输入必须立刻处理。"
                if include_time_signal
                else "内在叙事流的轻量心跳：没有新事件，也可以继续维持注意、旁路感知和行动倾向。"
            ),
            metadata={
                "reason": reason,
                "elapsed_seconds": round(elapsed, 1),
                "heartbeat": True,
                "time_signal": include_time_signal,
            },
        )



def _event_prompt_max_chars(section: dict, events: list[input_events.InputEvent], trigger_reason: str) -> int:
    default_max = int(section.get("event_prompt_max_chars", 3200) or 3200)
    tick_max = int(section.get("tick_prompt_max_chars", 1200) or 1200)
    is_tick_only = bool(events) and all(event.type == "time_tick" for event in events)
    if is_tick_only or "心跳" in str(trigger_reason or ""):
        return max(200, tick_max)
    return max(200, default_max)


def _log_inner_stream_update(
    character_id: str,
    *,
    enabled: bool,
    trigger_reason: str,
    events: list[input_events.InputEvent],
    before: str,
    after: str,
    raw: str,
    saved: bool,
    error: str = "",
) -> None:
    if not enabled:
        return
    try:
        os.makedirs(_LOGS_DIR, exist_ok=True)
        day = datetime.now().strftime("%Y%m%d")
        path = os.path.join(_LOGS_DIR, f"inner_stream-{character_id}-{day}.log")
        event_summary = input_events.format_events_for_prompt(events, max_chars=1200)
        timestamp = datetime.now().isoformat(timespec="seconds")
        with open(path, "a", encoding="utf-8") as file:
            file.write(f"\n===== {timestamp} trigger={trigger_reason} saved={saved} =====\n")
            if error:
                file.write(f"error: {error}\n")
            file.write("[events]\n")
            file.write((event_summary or "（无）") + "\n")
            file.write("[before]\n")
            file.write((before or "（空）").strip() + "\n")
            file.write("[after]\n")
            file.write((after or "（空）").strip() + "\n")
            if raw and raw.strip() != (after or "").strip():
                file.write("[raw]\n")
                file.write(raw.strip() + "\n")
    except Exception as exc:
        logger.debug("failed to write inner stream log: %s", exc)


def _event_type_has_content(event: input_events.InputEvent) -> bool:
    """Check if an event type carries meaningful new information."""
    if event.type in ("time_tick", "chat_environment"):
        return False  # heartbeats and QQ polling snapshots are never meaningful on their own
    if event.type == "text":
        content = str(event.content or "").strip()
        return bool(content) and content != "（无）"
    if event.type == "self_action":
        return True
    if event.type == "context_cache":
        return bool(event.content and "无" not in str(event.content))
    return True  # other types like vision/image/search/memory are content


def _events_digest(events: list[input_events.InputEvent]) -> str:
    """A lightweight digest to detect repeated event batches."""
    parts: list[str] = []
    for event in events[-8:]:
        if event.type == "time_tick":
            if (event.metadata or {}).get("time_signal"):
                elapsed = int(float((event.metadata or {}).get("elapsed_seconds") or 0.0))
                parts.append(f"time_tick:{elapsed}")
            continue
        content = str(event.content or "")[:120]
        meta = str(event.metadata.get("action") or event.metadata.get("source") or "")[:40]
        parts.append(f"{event.type}:{event.source}:{meta}:{content}")
    return "|".join(parts)
