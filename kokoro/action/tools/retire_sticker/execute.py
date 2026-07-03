"""Execution for retiring local sticker assets."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools import qq as qq_tool


def execute_retire_sticker(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    sticker_id = str(prepared.args.get("sticker_id") or "").strip()
    reason = str(prepared.args.get("reason") or "").strip()
    metadata = {"sticker_id": sticker_id, "reason": reason, "retired": False}
    if not sticker_id:
        return tool_spec.ToolResult("sticker_id is empty; sticker was not retired", status="failed", metadata=metadata)

    session = ctx.session
    actor = getattr(session, "character_name", "") if session is not None else ""
    item = qq_tool.retire_sticker(sticker_id, reason=reason, actor=actor)
    if not item:
        return tool_spec.ToolResult(
            f"sticker not found or could not be retired: {sticker_id}",
            status="failed",
            metadata=metadata,
        )

    return tool_spec.ToolResult(
        f"sticker retired: {sticker_id}",
        metadata={**metadata, "retired": True},
    )
