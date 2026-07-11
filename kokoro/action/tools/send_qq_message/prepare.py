"""Preparation stage for QQ message sending."""

from __future__ import annotations

from kokoro.action import model as action_model
from kokoro.action import tool_spec
from kokoro.core import lifecycle_debug


def prepare_message(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    args = dict(action.args)
    message = str(args.get("message") or args.get("content") or args.get("text") or "").strip()
    requested_conversation_id = str(
        args.get("conversation_id")
        or args.get("target")
        or args.get("channel")
        or ""
    ).strip()
    recent_conversation_id = str(ctx.get("recent_qq_conversation_id", "") or "").strip()
    conversation_id = requested_conversation_id or recent_conversation_id
    if recent_conversation_id and requested_conversation_id and requested_conversation_id != recent_conversation_id:
        lifecycle_debug.log(
            "send_qq_message.prepare.conversation_id_corrected",
            requested_conversation_id=requested_conversation_id,
            recent_conversation_id=recent_conversation_id,
        )
        conversation_id = recent_conversation_id
    reason = str(args.get("reason") or action.reason or "llm_decided").strip()
    args.update(
        {
            "message": message,
            "conversation_id": conversation_id,
            "reason": reason,
        }
    )
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=reason,
        metadata={
            "message_chars": len(message),
            "conversation_id": conversation_id,
        },
    )
