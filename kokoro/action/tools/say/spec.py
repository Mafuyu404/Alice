"""Registration for speech action tools."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools.say import after, execute, prepare


def register(registry: tool_spec.ActionToolRegistry) -> None:
    registry.register(
        tool_spec.ToolSpec(
            name="say",
            actions={"say"},
            prepare=prepare.prepare_say,
            execute=execute.execute_say,
            after=after.after_spoken_reply,
            timeout_seconds=180.0,
            max_parallel=1,
            default_visibility="public",
            default_result_policy="feed_back",
        )
    )
    registry.register(
        tool_spec.ToolSpec(
            name="say_precomputed",
            actions={"say_precomputed"},
            prepare=prepare.prepare_precomputed,
            execute=execute.execute_say_precomputed,
            after=after.after_spoken_reply,
            timeout_seconds=180.0,
            max_parallel=1,
            default_visibility="public",
            default_result_policy="feed_back",
        )
    )
    registry.register(
        tool_spec.ToolSpec(
            name="wait",
            actions={"wait", "stay_silent"},
            prepare=prepare.prepare_wait,
            execute=execute.execute_wait,
            timeout_seconds=5.0,
            max_parallel=4,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
