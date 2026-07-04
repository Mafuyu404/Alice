"""Textual time awareness for LLM autonomy."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TimeAwareness:
    """Tracks elapsed runtime facts and renders them as prompt material."""

    clock: object | None = None
    wall_clock: object | None = None
    started_at: float = field(default_factory=time.monotonic)
    last_inner_stream_at: float = 0.0
    last_llm_thought_at: float = 0.0
    last_external_input_at: float = 0.0
    last_self_action_at: float = 0.0
    last_tool_result_at: float = 0.0

    def mark_inner_stream(self) -> None:
        self.last_inner_stream_at = self._now()

    def mark_llm_thought(self) -> None:
        self.last_llm_thought_at = self._now()

    def mark_event(self, *, event_type: str = "") -> None:
        now = self._now()
        if event_type == "self_action":
            self.last_self_action_at = now
        elif event_type == "action_result":
            self.last_tool_result_at = now
        elif event_type != "time_tick":
            self.last_external_input_at = now

    def render(self, *, pending_lines: list[str] | None = None) -> str:
        now = self._now()
        wall = self._wall_now()
        parts = [
            f"Current wall time: {wall.isoformat(timespec='seconds')}",
            f"Runtime elapsed: {_fmt_seconds(now - self.started_at)}",
        ]
        if self.last_inner_stream_at:
            parts.append(f"Since last inner_stream update: {_fmt_seconds(now - self.last_inner_stream_at)}")
        else:
            parts.append("The inner_stream has not been updated in this runtime yet.")
        if self.last_llm_thought_at:
            parts.append(f"Since last LLM thought: {_fmt_seconds(now - self.last_llm_thought_at)}")
        if self.last_external_input_at:
            parts.append(f"Since last external input: {_fmt_seconds(now - self.last_external_input_at)}")
        if self.last_self_action_at:
            parts.append(f"Since last self action: {_fmt_seconds(now - self.last_self_action_at)}")
        if self.last_tool_result_at:
            parts.append(f"Since last tool result: {_fmt_seconds(now - self.last_tool_result_at)}")
        for line in pending_lines or []:
            text = str(line or "").strip()
            if text:
                parts.append(text)
        return "\n".join(parts)

    def _now(self) -> float:
        if self.clock is not None:
            return float(self.clock())
        return time.monotonic()

    def _wall_now(self) -> datetime:
        if self.wall_clock is not None:
            value = self.wall_clock()
            if isinstance(value, datetime):
                return value
        return datetime.now().astimezone()


def _fmt_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {sec}s"
