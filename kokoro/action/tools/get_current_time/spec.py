"""Registration for current time lookup."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools.get_current_time import execute


GET_CURRENT_TIME_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "Get the current date, time, and weekday when temporal context is needed.",
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
            name="get_current_time",
            actions={"get_current_time"},
            execute=execute.execute_get_current_time,
            schema=GET_CURRENT_TIME_SCHEMA,
            timeout_seconds=3.0,
            max_parallel=1,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
