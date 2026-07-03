"""Registration for screen observation action tools."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools.observe_screen import execute, prepare


LOOK_AT_SCREEN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "look_at_screen",
        "description": "Capture and analyze the current screen when visual context is needed.",
        "parameters": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "Optional focus or concrete question for the screen observation.",
                },
            },
            "required": [],
        },
    },
}


def register(registry: tool_spec.ActionToolRegistry) -> None:
    registry.register(
        tool_spec.ToolSpec(
            name="observe_screen",
            actions={"observe_screen", "look_at_screen"},
            prepare=prepare.prepare_focus,
            execute=execute.execute_observe_screen,
            schema=LOOK_AT_SCREEN_SCHEMA,
            timeout_seconds=45.0,
            max_parallel=1,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
