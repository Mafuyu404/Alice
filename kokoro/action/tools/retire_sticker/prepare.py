"""Preparation stage for sticker retirement."""

from __future__ import annotations

from kokoro.action import model as action_model
from kokoro.action import tool_spec


def prepare_retire_sticker(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    args = dict(action.args)
    sticker_id = str(args.get("sticker_id") or args.get("image_id") or args.get("id") or "").strip()
    reason = str(args.get("reason") or action.reason or "").strip()
    args.update({"sticker_id": sticker_id, "reason": reason})
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=reason or "prepare sticker retirement",
        metadata={"sticker_id": sticker_id, "reason": reason},
    )
