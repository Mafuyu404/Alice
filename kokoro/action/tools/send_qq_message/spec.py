"""Registration for QQ message sending."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools.send_qq_message import execute, prepare


SEND_QQ_MESSAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_qq_message",
        "description": (
            "当角色已经形成具体表达冲动时，通过已连接的 QQ 通道发送一条自然文本。"
            "一次只承接一句话，不用于批量发送重复关心。"
        ),
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
