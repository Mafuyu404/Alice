"""Action handlers for memory-related side effects."""

from __future__ import annotations

from typing import Callable

from kokoro.action import model as action_model


def conversation_memory_handler(*, session) -> Callable[[action_model.Action], str]:
    def handle(action: action_model.Action) -> str:
        trigger_text = str(action.args.get("trigger_text") or "").strip()
        reply = str(action.args.get("reply") or "").strip()
        if not trigger_text and not reply:
            return "memory skipped: empty turn"
        session.remember(trigger_text, reply, async_store=True)
        return "conversation memory queued"

    return handle
