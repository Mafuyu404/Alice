"""Lifecycle core primitives.

This package owns the inner narrative stream and unified runtime events.
Dialogue, QQ, tools, memory, and autonomous behavior should feed through these
core primitives instead of treating chat as the root loop.
"""

from .input_events import (
    EventSubscriber,
    InputEvent,
    InputEventBus,
    InputHandler,
    InputLifetime,
    InputPriority,
    InputTypeRegistry,
    PrivacyMark,
    build_action_result_event,
    build_chat_environment_event,
    build_self_action_event,
    build_text_event,
    build_time_tick_event,
    build_web_search_event,
    default_registry,
    format_events_for_prompt,
)
from .inner_stream import InnerStream, InnerStreamLoop

__all__ = [
    "EventSubscriber",
    "InputEvent",
    "InputEventBus",
    "InputHandler",
    "InputLifetime",
    "InputPriority",
    "InputTypeRegistry",
    "InnerStream",
    "InnerStreamLoop",
    "PrivacyMark",
    "build_action_result_event",
    "build_chat_environment_event",
    "build_self_action_event",
    "build_text_event",
    "build_time_tick_event",
    "build_web_search_event",
    "default_registry",
    "format_events_for_prompt",
]
