"""Execution for foreground application lookup."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools import observe_screen


def execute_get_current_app(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    try:
        fg = observe_screen.get_foreground_app()
    except Exception as exc:
        return tool_spec.ToolResult(
            f"failed to get foreground app: {exc}",
            status="failed",
            metadata={"error": str(exc), "foreground_found": False},
        )
    if not fg or not fg.get("title"):
        return tool_spec.ToolResult(
            "foreground window could not be determined",
            status="failed",
            metadata={"foreground_found": False},
        )
    title = str(fg.get("title") or "")
    process = str(fg.get("process") or "")
    return tool_spec.ToolResult(
        f"Foreground window: {title} (process: {process})",
        metadata={"foreground_found": True, "title": title, "process": process},
    )
