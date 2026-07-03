"""Post-execution hooks for sticker retirement."""

from __future__ import annotations

from kokoro.action import tool_spec


def after_retire_sticker(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
    result: tool_spec.ToolResult,
) -> None:
    if result.status != "success":
        return
    session = ctx.session
    record = getattr(session, "record_self_action", None) if session is not None else None
    if not callable(record):
        return
    sticker_id = str(result.metadata.get("sticker_id") or prepared.args.get("sticker_id") or "").strip()
    reason = str(result.metadata.get("reason") or prepared.args.get("reason") or "").strip()
    record(
        f"I decided to stop using sticker {sticker_id}. Reason: {reason or 'not suitable for continued use'}",
        source="sticker_library",
        action="retire_sticker",
        metadata={"sticker_id": sticker_id, "reason": reason},
    )
