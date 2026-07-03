"""Prepare web search actions."""

from __future__ import annotations

from kokoro.action import model as action_model
from kokoro.action import tool_spec


def prepare_query(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    args = dict(action.args)
    query = str(
        args.get("query")
        or args.get("topic")
        or args.get("question")
        or args.get("intent")
        or ""
    ).strip()
    if not query:
        query = action.reason.strip()
    args["query"] = query
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=action.reason or "prepare web search query",
        metadata={"prepared_query": query},
    )
