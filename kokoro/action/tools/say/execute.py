"""Execution entry points for speech action tools."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools.say import runtime


def execute_say(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    return runtime.execute_say(ctx, prepared)


def execute_say_precomputed(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    return runtime.execute_say_precomputed(ctx, prepared)


def execute_wait(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    return runtime.execute_wait(ctx, prepared)

__all__ = [
    "execute_say",
    "execute_say_precomputed",
    "execute_wait",
]
