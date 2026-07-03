"""Direct routing helpers for VTube Studio actions."""

from __future__ import annotations

import re


def direct_route(text: str, available_tools: list[str]) -> dict | None:
    compact = re.sub(r"[\s\W_]+", "", text or "").lower()
    if not compact:
        return None
    vts_markers = ("vts", "live2d", "皮套", "表情", "身体", "动起来", "动作", "摇头", "晃脑", "点头", "笑一笑", "笑一下")
    if not any(marker.lower() in compact for marker in vts_markers):
        return None
    if "vts_motion" in available_tools and any(marker in compact for marker in ("摇头", "晃脑", "身体", "动起来", "动作", "点头")):
        motion = "shake" if any(marker in compact for marker in ("摇头", "晃脑", "晃一晃")) else "nod" if "点头" in compact else "sway"
        return {
            "tool_name": "vts_motion",
            "reason": "direct_vts_motion_request",
            "arguments": {
                "motion": motion,
                "intensity": 0.9,
                "duration_seconds": 4.0,
                "reason": "用户要求测试 Live2D 身体动作",
            },
        }
    if "vts_expression" in available_tools and any(marker in compact for marker in ("笑", "表情", "眨眼", "撇嘴")):
        expression = "smile"
        if "撇嘴" in compact:
            expression = "pout"
        elif "眨眼" in compact:
            expression = "wink"
        return {
            "tool_name": "vts_expression",
            "reason": "direct_vts_expression_request",
            "arguments": {"expression": expression, "intensity": 0.9, "duration_seconds": 3.0},
        }
    return None
