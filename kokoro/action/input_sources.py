"""Input source boundaries for runtime adapters."""

from __future__ import annotations

from kokoro.core import input_events


def publish_speech_text(
    session,
    text: str,
    *,
    speaker: str,
    reason: str,
    priority: input_events.InputPriority = "high",
) -> input_events.InputEvent | None:
    """Publish a user speech segment as an InputEvent."""
    return session.record_input_event(
        text,
        source="speech",
        event_type="text",
        metadata={
            "speaker": speaker,
            "interrupts_prior_focus": True,
            "attention_reset": "latest_user_input",
            "reason": reason,
            "input_source": "stt",
        },
        priority=priority,
    )


def publish_observation_text(
    session,
    text: str,
    *,
    source: str,
    metadata: dict | None = None,
    priority: input_events.InputPriority = "normal",
) -> input_events.InputEvent | None:
    """Publish an observation from an input/tool adapter."""
    return session.record_input_event(
        text,
        source=source,
        event_type="text",
        metadata={"input_source": source, **(metadata or {})},
        priority=priority,
    )


def publish_debug_text(
    session,
    text: str,
    *,
    mode: str,
    debug_id: str,
    seq: int,
    priority: input_events.InputPriority = "urgent",
) -> input_events.InputEvent | None:
    """Publish a high-priority plain-text debug input."""
    return session.record_input_event(
        text,
        source="debug_text",
        event_type="text",
        metadata={
            "input_source": "debug",
            "debug": True,
            "debug_id": debug_id,
            "seq": seq,
            "mode": mode,
            "speaker": "debug",
            "interrupts_prior_focus": True,
            "attention_reset": "debug_input",
        },
        priority=priority,
        lifetime="ephemeral" if mode == "command" else "session",
    )
