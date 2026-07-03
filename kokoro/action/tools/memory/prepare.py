"""Preparation stage for memory action tools."""

from __future__ import annotations

from kokoro.action import model as action_model
from kokoro.action import tool_spec


def prepare_search_memory(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    args = dict(action.args)
    query = str(
        args.get("query")
        or args.get("topic")
        or args.get("question")
        or args.get("trigger_text")
        or action.reason
        or ""
    ).strip()
    args["query"] = query
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=action.reason or "prepare memory search query",
        metadata={"prepared_query": query},
    )


def prepare_save_to_memory(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    args = dict(action.args)
    content = str(
        args.get("content")
        or args.get("memory_note")
        or args.get("note")
        or args.get("event")
        or ""
    ).strip()
    importance = str(args.get("importance") or "medium").strip().lower() or "medium"
    if importance not in {"high", "medium", "low"}:
        importance = "medium"
    args["content"] = content
    args["importance"] = importance
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=action.reason or "prepare memory write",
        metadata={"content_chars": len(content), "importance": importance},
    )


def prepare_conversation_memory(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    args = dict(action.args)
    trigger_text = str(args.get("trigger_text") or args.get("text") or "").strip()
    reply = str(args.get("reply") or "").strip()
    speaker_id = str(args.get("speaker_id") or getattr(ctx.session, "character_id", "") or "").strip()
    args.update(
        {
            "trigger_text": trigger_text,
            "reply": reply,
            "speaker_id": speaker_id,
        }
    )
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=action.reason or "prepare conversation memory",
        metadata={
            "trigger_chars": len(trigger_text),
            "reply_chars": len(reply),
            "speaker_id": speaker_id,
        },
    )
