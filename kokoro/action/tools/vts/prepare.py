"""Preparation stage for VTube Studio tools."""

from __future__ import annotations

from typing import Any

from kokoro.action import model as action_model
from kokoro.action import tool_spec

_EXPRESSION_ALIASES = {
    "confused": "doubt",
    "confuse": "doubt",
    "thinking_face": "thinking",
    "think": "thinking",
    "smiling": "smile",
    "happy_smile": "happy",
}


def prepare_expression(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    args = dict(action.args)
    expression = _normalize_expression(
        args.get("expression") or args.get("emotion") or args.get("face") or args.get("name") or ""
    )
    intensity = _clamp_float(args.get("intensity", 1.0), 0.0, 1.0)
    duration = _clamp_float(args.get("duration_seconds", args.get("duration", 0)), 0.0, 30.0)
    args.update(
        {
            "expression": expression,
            "intensity": intensity,
            "duration_seconds": duration,
        }
    )
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=action.reason or "prepare VTS expression",
        metadata={"expression": expression, "intensity": intensity, "duration_seconds": duration},
    )


def prepare_motion(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    args = dict(action.args)
    motion = str(args.get("motion") or args.get("body_motion") or args.get("gesture") or "idle").strip().lower()
    intensity = _clamp_float(args.get("intensity", 0.75), 0.0, 1.0)
    duration = _clamp_float(args.get("duration_seconds", args.get("duration", 4.0)), 0.5, 12.0)
    reason = str(args.get("reason") or action.reason or motion or "vts_motion").strip()
    args.update(
        {
            "motion": motion,
            "intensity": intensity,
            "duration_seconds": duration,
            "reason": reason,
        }
    )
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=reason,
        metadata={"motion": motion, "intensity": intensity, "duration_seconds": duration},
    )


def _normalize_expression(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _EXPRESSION_ALIASES.get(raw, _EXPRESSION_ALIASES.get(raw.lower(), raw))


def _clamp_float(value: Any, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))
