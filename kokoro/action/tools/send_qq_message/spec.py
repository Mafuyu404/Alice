"""Registration for QQ message sending."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools.send_qq_message import execute, prepare


SEND_QQ_MESSAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_qq_message",
        "description": "Send a text message through the connected QQ channel as the character.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Exact text to send to QQ, without action narration.",
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Optional target such as group:123 or private:456.",
                },
                "reason": {
                    "type": "string",
                    "description": "Short internal reason for why sending now is natural.",
                },
            },
            "required": ["message"],
        },
    },
}


def register(registry: tool_spec.ActionToolRegistry) -> None:
    registry.register(
        tool_spec.ToolSpec(
            name="send_qq_message",
            actions={"send_qq_message"},
            prepare=prepare.prepare_message,
            execute=execute.execute_send_qq_message,
            schema=SEND_QQ_MESSAGE_SCHEMA,
            timeout_seconds=10.0,
            max_parallel=2,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
