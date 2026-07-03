"""Registration for VTube Studio action tools."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools.vts import execute, prepare


VTS_EXPRESSION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "vts_expression",
        "description": "Control the Live2D facial expression for intentional emphasis.",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "enum": [
                        "smile", "happy", "sad", "angry", "surprised",
                        "tired", "thinking", "shy", "excited", "wink",
                        "pout", "sigh", "cry", "doubt", "confused", "awkward", "neutral",
                    ],
                    "description": "Expression to show.",
                },
                "intensity": {
                    "type": "number",
                    "description": "Expression intensity from 0.0 to 1.0.",
                    "minimum": 0,
                    "maximum": 1,
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "Duration in seconds; 0 means keep until replaced.",
                    "minimum": 0,
                    "maximum": 30,
                },
            },
            "required": ["expression"],
        },
    },
}


VTS_MOTION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "vts_motion",
        "description": "Control Live2D body or head motion.",
        "parameters": {
            "type": "object",
            "properties": {
                "motion": {
                    "type": "string",
                    "enum": [
                        "smile", "happy", "nod", "shake", "sway", "bounce",
                        "excited", "shy", "pout", "sad", "thinking", "idle",
                    ],
                    "description": "Motion type to perform.",
                },
                "intensity": {
                    "type": "number",
                    "description": "Motion intensity from 0.0 to 1.0.",
                    "minimum": 0,
                    "maximum": 1,
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "Motion duration in seconds.",
                    "minimum": 0.5,
                    "maximum": 12,
                },
                "reason": {
                    "type": "string",
                    "description": "Short internal reason for the motion.",
                },
            },
            "required": ["motion"],
        },
    },
}


def register(registry: tool_spec.ActionToolRegistry) -> None:
    registry.register(
        tool_spec.ToolSpec(
            name="vts_expression",
            actions={"vts_expression"},
            prepare=prepare.prepare_expression,
            execute=execute.execute_expression,
            schema=VTS_EXPRESSION_SCHEMA,
            timeout_seconds=8.0,
            max_parallel=1,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
    registry.register(
        tool_spec.ToolSpec(
            name="vts_motion",
            actions={"vts_motion"},
            prepare=prepare.prepare_motion,
            execute=execute.execute_motion,
            schema=VTS_MOTION_SCHEMA,
            timeout_seconds=8.0,
            max_parallel=1,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
