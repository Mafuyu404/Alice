"""Registration for web search action tools."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools.search_web import after, execute, prepare


WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "Search public web information after preparing a focused query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "topic": {"type": "string"},
                "question": {"type": "string"},
            },
            "required": ["query"],
        },
    },
}


def register(registry: tool_spec.ActionToolRegistry) -> None:
    registry.register(
        tool_spec.ToolSpec(
            name="search_web",
            actions={"search_web"},
            prepare=prepare.prepare_query,
            execute=execute.execute_search_web,
            after=after.after_search_web,
            schema=WEB_SEARCH_SCHEMA,
            timeout_seconds=45.0,
            max_parallel=2,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
