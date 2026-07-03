"""Execute screen observation actions."""

from __future__ import annotations

import logging

from kokoro.action import tool_spec
from kokoro.action.tools.observe_screen import screen_interest
from kokoro.action.tools.observe_screen import vision
from kokoro.core import prompts

logger = logging.getLogger(__name__)


def execute_observe_screen(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    timeout = ctx.get("tool_timeout", 45)
    focus = str(prepared.args.get("focus") or "").strip()
    try:
        foreground = vision.get_foreground_app()
    except Exception:
        foreground = None

    metadata = {
        "focus": focus,
        "foreground_app": _foreground_name(foreground),
        "privacy_blocked": False,
    }
    if screen_interest.foreground_is_private(foreground):
        return tool_spec.ToolResult(
            content=prompts.get(
                "tool_calling.privacy_blocked",
                "screen observation skipped because the foreground window may contain private content",
            ),
            status="skipped",
            metadata={**metadata, "privacy_blocked": True},
        )

    prompt_text = focus or prompts.get(
        "tool_handlers.look_at_screen_default",
        "Describe the current desktop screenshot, including the foreground window and key visible information.",
    )
    try:
        result = vision.detect_desktop(prompt=prompt_text, timeout=timeout)
    except Exception as exc:
        logger.exception("observe_screen failed")
        error = f"{type(exc).__name__}: {exc}"
        return tool_spec.ToolResult(
            content=prompts.format_prompt("tool_calling.tool_error", error=error),
            status="failed",
            metadata={**metadata, "error": error},
        )

    result_text = str(result or "").strip()
    if not result_text:
        return tool_spec.ToolResult(
            content=prompts.get("tool_handlers.empty_screen_content", "screen observation returned no usable content"),
            status="failed",
            metadata={**metadata, "empty_result": True},
        )

    session = ctx.session
    if session and hasattr(session, "add_screen_context"):
        session.add_screen_context(result_text[:600])

    prefix = prompts.get("tool_calling.look_at_screen_prefix", "screen observation result:\n")
    return tool_spec.ToolResult(
        content=prefix + result_text[:2000],
        metadata={
            **metadata,
            "screen_context_added": bool(session and hasattr(session, "add_screen_context")),
            "result_chars": len(result_text),
        },
    )


def _foreground_name(foreground: object) -> str:
    if foreground is None:
        return ""
    if isinstance(foreground, dict):
        return str(foreground.get("process") or foreground.get("title") or foreground.get("app") or "").strip()
    return str(getattr(foreground, "process", "") or getattr(foreground, "title", "") or foreground).strip()
