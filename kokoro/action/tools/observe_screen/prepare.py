"""Prepare screen observation actions."""

from __future__ import annotations

from kokoro.action import model as action_model
from kokoro.action import tool_spec


def prepare_focus(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    args = dict(action.args)
    focus = str(
        args.get("focus")
        or args.get("question")
        or args.get("topic")
        or args.get("intent")
        or ""
    ).strip()
    if not focus:
        focus = "Observe the current screen and summarize the visible key information."
    args["focus"] = focus
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=action.reason or "prepare screen observation focus",
        metadata={"prepared_focus": focus},
    )
