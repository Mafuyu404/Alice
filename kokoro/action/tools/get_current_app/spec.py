"""Registration for foreground application lookup."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools.get_current_app import execute


GET_CURRENT_APP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_current_app",
        "description": "Get the foreground application and process without reading screen contents.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def register(registry: tool_spec.ActionToolRegistry) -> None:
    registry.register(
        tool_spec.ToolSpec(
            name="get_current_app",
            actions={"get_current_app"},
            execute=execute.execute_get_current_app,
            schema=GET_CURRENT_APP_SCHEMA,
            timeout_seconds=3.0,
            max_parallel=1,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
