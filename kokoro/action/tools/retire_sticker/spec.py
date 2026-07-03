"""Registration for sticker retirement."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools.retire_sticker import after, execute, prepare


RETIRE_STICKER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "retire_sticker",
        "description": "Retire a local sticker when the character decides it should no longer be used.",
        "parameters": {
            "type": "object",
            "properties": {
                "sticker_id": {
                    "type": "string",
                    "description": "Sticker id to retire.",
                },
                "reason": {
                    "type": "string",
                    "description": "Short reason for retiring this sticker.",
                },
            },
            "required": ["sticker_id"],
        },
    },
}


def register(registry: tool_spec.ActionToolRegistry) -> None:
    registry.register(
        tool_spec.ToolSpec(
            name="retire_sticker",
            actions={"retire_sticker"},
            prepare=prepare.prepare_retire_sticker,
            execute=execute.execute_retire_sticker,
            after=after.after_retire_sticker,
            schema=RETIRE_STICKER_SCHEMA,
            timeout_seconds=10.0,
            max_parallel=1,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
