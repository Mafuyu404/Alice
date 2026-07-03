"""Registration for memory action tools."""

from __future__ import annotations

from kokoro.action import tool_spec
from . import execute, prepare


SEARCH_MEMORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_memory",
        "description": "Search long-term memory for context relevant to the current situation.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language description of what to remember or retrieve.",
                },
            },
            "required": ["query"],
        },
    },
}


SAVE_TO_MEMORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "save_to_memory",
        "description": "Save important information to long-term memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Concise memory content to store.",
                },
                "importance": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Importance level for the memory.",
                },
            },
            "required": ["content"],
        },
    },
}


WRITE_CONVERSATION_MEMORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_conversation_memory",
        "description": "Queue a completed conversation turn for memory sedimentation.",
        "parameters": {
            "type": "object",
            "properties": {
                "trigger_text": {"type": "string"},
                "reply": {"type": "string"},
                "speaker_id": {"type": "string"},
            },
            "required": ["reply"],
        },
    },
}


def register(registry: tool_spec.ActionToolRegistry) -> None:
    registry.register(
        tool_spec.ToolSpec(
            name="search_memory",
            actions={"search_memory"},
            prepare=prepare.prepare_search_memory,
            execute=execute.execute_search_memory,
            schema=SEARCH_MEMORY_SCHEMA,
            timeout_seconds=10.0,
            max_parallel=2,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
    registry.register(
        tool_spec.ToolSpec(
            name="save_to_memory",
            actions={"save_to_memory"},
            prepare=prepare.prepare_save_to_memory,
            execute=execute.execute_save_to_memory,
            schema=SAVE_TO_MEMORY_SCHEMA,
            timeout_seconds=10.0,
            max_parallel=1,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
    registry.register(
        tool_spec.ToolSpec(
            name="memory",
            actions={"write_conversation_memory"},
            prepare=prepare.prepare_conversation_memory,
            execute=execute.execute_conversation_memory,
            schema=WRITE_CONVERSATION_MEMORY_SCHEMA,
            timeout_seconds=10.0,
            max_parallel=2,
            default_visibility="private",
            default_result_policy="record_only",
        )
    )
