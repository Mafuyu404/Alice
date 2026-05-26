"""Unified input events for autonomous runtime layers.

Input handlers convert raw sources into ``InputEvent`` objects.  The event bus
only fan-outs events; interpretation stays in dialogue, inner stream, and later
activity layers.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Literal


InputPriority = Literal["low", "normal", "high", "urgent"]
InputLifetime = Literal["ephemeral", "session", "memorize_candidate"]


@dataclass(frozen=True)
class PrivacyMark:
    private: bool = False
    reason: str = ""

    @classmethod
    def from_raw(cls, value: "PrivacyMark | dict | None") -> "PrivacyMark":
        if isinstance(value, PrivacyMark):
            return value
        if isinstance(value, dict):
            return cls(
                private=bool(value.get("private", False)),
                reason=str(value.get("reason", "") or ""),
            )
        return cls()


@dataclass(frozen=True)
class InputEvent:
    type: str
    source: str
    content: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat())
    metadata: dict = field(default_factory=dict)
    privacy: PrivacyMark = field(default_factory=PrivacyMark)
    priority: InputPriority = "normal"
    lifetime: InputLifetime = "session"

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", str(self.type or "text").strip() or "text")
        object.__setattr__(self, "source", str(self.source or "unknown").strip() or "unknown")
        object.__setattr__(self, "content", str(self.content or "").strip())
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        object.__setattr__(self, "privacy", PrivacyMark.from_raw(self.privacy))
        if self.priority not in ("low", "normal", "high", "urgent"):
            object.__setattr__(self, "priority", "normal")
        if self.lifetime not in ("ephemeral", "session", "memorize_candidate"):
            object.__setattr__(self, "lifetime", "session")

    def visible_content(self) -> str:
        if self.privacy.private:
            reason = f"：{self.privacy.reason}" if self.privacy.reason else ""
            return f"出现了一段不适合读取正文的输入{reason}。"
        return self.content


InputHandler = Callable[..., InputEvent]
EventSubscriber = Callable[[InputEvent], None]


class InputTypeRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, InputHandler] = {}

    def register(self, input_type: str, handler: InputHandler) -> None:
        key = str(input_type or "").strip()
        if not key:
            raise ValueError("input type cannot be empty")
        self._handlers[key] = handler

    def create(self, input_type: str, *args, **kwargs) -> InputEvent:
        key = str(input_type or "").strip()
        if key not in self._handlers:
            raise KeyError(key)
        return self._handlers[key](*args, **kwargs)

    def registered_types(self) -> list[str]:
        return sorted(self._handlers)


class InputEventBus:
    """Thread-safe fan-out bus with a replayable bounded event queue."""

    def __init__(self, *, max_queue: int = 200) -> None:
        self._subscribers: list[EventSubscriber] = []
        self._lock = threading.Lock()
        self._queue: queue.Queue[InputEvent] = queue.Queue(maxsize=max(1, max_queue))

    def subscribe(self, callback: EventSubscriber) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def publish(self, event: InputEvent) -> None:
        if not isinstance(event, InputEvent):
            raise TypeError("event must be InputEvent")
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(event)
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            callback(event)

    def drain(self, max_items: int | None = None) -> list[InputEvent]:
        events: list[InputEvent] = []
        while max_items is None or len(events) < max_items:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events

    def snapshot(self, max_items: int | None = None) -> list[InputEvent]:
        with self._queue.mutex:
            events = list(self._queue.queue)
        if max_items is not None:
            return events[-max(0, int(max_items)):]
        return events


def build_text_event(
    content: str,
    *,
    source: str = "text",
    metadata: dict | None = None,
    privacy: PrivacyMark | dict | None = None,
    priority: InputPriority = "normal",
    lifetime: InputLifetime = "session",
) -> InputEvent:
    return InputEvent(
        type="text",
        source=source,
        content=content,
        metadata=metadata or {},
        privacy=PrivacyMark.from_raw(privacy),
        priority=priority,
        lifetime=lifetime,
    )


def build_self_action_event(
    content: str,
    *,
    source: str = "self",
    action: str = "",
    metadata: dict | None = None,
    lifetime: InputLifetime = "session",
) -> InputEvent:
    merged = dict(metadata or {})
    if action:
        merged.setdefault("action", action)
    return InputEvent(
        type="self_action",
        source=source,
        content=content,
        metadata=merged,
        priority="normal",
        lifetime=lifetime,
    )


def build_time_tick_event(
    content: str = "",
    *,
    source: str = "system_clock",
    metadata: dict | None = None,
    priority: InputPriority = "low",
    lifetime: InputLifetime = "ephemeral",
) -> InputEvent:
    merged = dict(metadata or {})
    merged.setdefault("monotonic", round(time.monotonic(), 3))
    return InputEvent(
        type="time_tick",
        source=source,
        content=content or "时间自然流逝了一段。",
        metadata=merged,
        priority=priority,
        lifetime=lifetime,
    )


def build_chat_environment_event(
    content: str,
    *,
    source: str = "qq",
    metadata: dict | None = None,
    privacy: PrivacyMark | dict | None = None,
    priority: InputPriority = "normal",
    lifetime: InputLifetime = "session",
) -> InputEvent:
    return InputEvent(
        type="chat_environment",
        source=source,
        content=content,
        metadata=metadata or {},
        privacy=PrivacyMark.from_raw(privacy),
        priority=priority,
        lifetime=lifetime,
    )


def build_web_search_event(
    content: str,
    *,
    source: str = "web_search",
    metadata: dict | None = None,
    privacy: PrivacyMark | dict | None = None,
    priority: InputPriority = "normal",
    lifetime: InputLifetime = "session",
) -> InputEvent:
    return InputEvent(
        type="web_search",
        source=source,
        content=content,
        metadata=metadata or {},
        privacy=PrivacyMark.from_raw(privacy),
        priority=priority,
        lifetime=lifetime,
    )


def format_events_for_prompt(events: Iterable[InputEvent], *, max_chars: int = 3000) -> str:
    lines: list[str] = []
    now = datetime.now(timezone.utc).astimezone()
    for event in events:
        meta = []
        if event.metadata.get("speaker"):
            meta.append(f"speaker={event.metadata.get('speaker')}")
        if event.metadata.get("action"):
            meta.append(f"action={event.metadata.get('action')}")
        age = _event_age_text(event.timestamp, now)
        if age:
            meta.append(f"age={age}")
        meta_text = f" ({', '.join(meta)})" if meta else ""
        lines.append(
            f"- [{event.timestamp}] type={event.type} source={event.source} "
            f"priority={event.priority}{meta_text}: {event.visible_content()}"
        )
    text = "\n".join(lines)
    return text[:max(200, max_chars)].strip()


def _event_age_text(timestamp: str, now: datetime) -> str:
    try:
        ts = datetime.fromisoformat(str(timestamp or ""))
    except Exception:
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=now.tzinfo)
    seconds = max(0.0, (now - ts.astimezone(now.tzinfo)).total_seconds())
    if seconds < 1:
        return "just_now"
    if seconds < 90:
        return f"{seconds:.0f}s_ago"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m_ago"
    return f"{seconds / 3600:.1f}h_ago"


def default_registry() -> InputTypeRegistry:
    registry = InputTypeRegistry()
    registry.register("text", build_text_event)
    registry.register("chat_environment", build_chat_environment_event)
    registry.register("time_tick", build_time_tick_event)
    registry.register("web_search", build_web_search_event)
    return registry
