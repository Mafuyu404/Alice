"""Execution for VTube Studio action tools."""

from __future__ import annotations

import asyncio
import threading

from kokoro.action import tool_spec

_revert_timer: threading.Timer | None = None
_revert_lock = threading.Lock()


def execute_expression(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    ctrl = ctx.get("vts_controller")
    expr = str(prepared.args.get("expression") or "").strip()
    intensity = float(prepared.args.get("intensity", 1.0) or 1.0)
    duration = float(prepared.args.get("duration_seconds", 0.0) or 0.0)
    metadata = {
        "expression": expr,
        "intensity": intensity,
        "duration_seconds": duration,
        "applied": False,
    }
    if ctrl is None:
        return tool_spec.ToolResult("VTS controller is not connected", status="failed", metadata=metadata)
    if not expr:
        return tool_spec.ToolResult("VTS expression is empty", status="failed", metadata=metadata)
    if not ctrl.has_expression(expr):
        return tool_spec.ToolResult(f"unknown VTS expression: {expr}", status="failed", metadata=metadata)

    arbiter = ctx.get("vts_arbiter")
    params = ctrl.get_expression_params(expr, intensity)
    if arbiter is not None:
        arbiter.set_layer("tool", params)
        if duration > 0:
            _schedule_timer(duration, lambda: _clear_arbiter_layer(arbiter))
        return tool_spec.ToolResult(f"VTS expression applied: {expr}", metadata={**metadata, "applied": True})

    loop: asyncio.AbstractEventLoop | None = ctx.get("event_loop")
    if loop is None or loop.is_closed():
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return tool_spec.ToolResult("no event loop available for VTS", status="failed", metadata=metadata)

    async def _apply() -> None:
        await ctrl.set_expression(expr, intensity)

    future = asyncio.run_coroutine_threadsafe(_apply(), loop)

    if duration > 0:
        revert_expr = ctx.get("vts_revert_expression", "neutral")

        async def _revert() -> None:
            await ctrl.set_expression(revert_expr, 1.0)

        def _delayed_revert() -> None:
            try:
                asyncio.run_coroutine_threadsafe(_revert(), loop)
            except Exception:
                pass

        _schedule_timer(duration, _delayed_revert)

    try:
        future.result(timeout=5)
    except Exception as exc:
        return tool_spec.ToolResult(
            f"VTS expression failed: {exc}",
            status="failed",
            metadata={**metadata, "error": str(exc)},
        )

    return tool_spec.ToolResult(f"VTS expression applied: {expr}", metadata={**metadata, "applied": True})


def execute_motion(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    body_driver = ctx.get("vts_body_driver")
    arbiter = ctx.get("vts_arbiter")
    motion = str(prepared.args.get("motion") or "idle").strip().lower()
    intensity = float(prepared.args.get("intensity", 0.75) or 0.75)
    duration = float(prepared.args.get("duration_seconds", 4.0) or 4.0)
    reason = str(prepared.args.get("reason") or motion or "vts_motion").strip()
    metadata = {
        "motion": motion,
        "intensity": intensity,
        "duration_seconds": duration,
        "reason": reason,
        "applied": False,
    }
    if body_driver is None and arbiter is None:
        return tool_spec.ToolResult("VTS body control is not connected", status="failed", metadata=metadata)

    if body_driver is not None and hasattr(body_driver, "play_direct_motion"):
        body_driver.play_direct_motion(
            motion,
            intensity=intensity,
            duration=duration,
            reason=reason,
        )
        return tool_spec.ToolResult(f"VTS motion applied: {motion}", metadata={**metadata, "applied": True})

    return tool_spec.ToolResult(
        "VTS body driver is not enabled",
        status="failed",
        metadata=metadata,
    )


def _schedule_timer(duration: float, callback) -> None:
    global _revert_timer
    with _revert_lock:
        if _revert_timer and _revert_timer.is_alive():
            _revert_timer.cancel()
        _revert_timer = threading.Timer(duration, callback)
        _revert_timer.daemon = True
        _revert_timer.start()


def _clear_arbiter_layer(arbiter) -> None:
    try:
        arbiter.clear_layer("tool")
    except Exception:
        pass
