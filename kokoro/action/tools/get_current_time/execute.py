"""Execution for current time lookup."""

from __future__ import annotations

import datetime

from kokoro.action import tool_spec


def execute_get_current_time(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    now = datetime.datetime.now()
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekday = weekdays[now.weekday()]
    iso_local = now.isoformat(timespec="seconds")
    return tool_spec.ToolResult(
        f"Current local time: {now.year}-{now.month:02d}-{now.day:02d} {weekday} "
        f"{now.hour:02d}:{now.minute:02d}:{now.second:02d}",
        metadata={
            "iso_local": iso_local,
            "year": now.year,
            "month": now.month,
            "day": now.day,
            "weekday": weekday,
            "hour": now.hour,
            "minute": now.minute,
            "second": now.second,
        },
    )
