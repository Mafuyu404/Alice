"""Execution for QQ message sending."""

from __future__ import annotations

import logging

from kokoro.action import tool_spec

logger = logging.getLogger(__name__)


def execute_send_qq_message(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    args = prepared.args
    message = str(args.get("message") or "").strip()
    conversation_id = str(args.get("conversation_id") or "").strip()
    reason = str(args.get("reason") or prepared.reason or "llm_decided").strip()
    metadata = {
        "message_chars": len(message),
        "conversation_id": conversation_id,
        "reason": reason,
    }
    if prepared.metadata.get("blocked"):
        return tool_spec.ToolResult(
            f"QQ message blocked by send audit: {reason}",
            status="skipped",
            metadata={**metadata, "sent": False, "suppress_feedback": True, "blocked": True},
        )
    if not message:
        return tool_spec.ToolResult(
            "QQ message is empty; nothing was sent.",
            status="failed",
            metadata={**metadata, "sent": False},
        )

    sender = ctx.get("qq_send_message")
    if not callable(sender):
        return tool_spec.ToolResult(
            "QQ channel is not connected; message was not sent.",
            status="failed",
            metadata={**metadata, "sent": False, "error": "sender_unavailable"},
        )

    try:
        result = sender(message, conversation_id=conversation_id, reason=reason)
    except Exception as exc:
        logger.warning("send_qq_message failed: %s", exc)
        error = f"{type(exc).__name__}: {exc}"
        return tool_spec.ToolResult(
            f"QQ send failed: {error}",
            status="failed",
            metadata={**metadata, "sent": False, "error": error},
        )
    result_text = str(result or "").strip()
    if result_text.lower().startswith("qq send failed"):
        return tool_spec.ToolResult(
            result_text,
            status="failed",
            metadata={**metadata, "sent": False, "error": result_text},
        )
    return tool_spec.ToolResult(
        result_text or "QQ send completed.",
        metadata={**metadata, "sent": True, "suppress_feedback": True, "self_expression": True},
    )
