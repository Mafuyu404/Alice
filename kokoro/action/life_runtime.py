"""Action runtime assembly for the life runtime.

This module creates the executable tool boundary without starting the legacy
autonomous decision loop. LifeRuntime owns the choice of tools; ActionRuntime
only executes registered capabilities and publishes feedback.
"""

from __future__ import annotations

from typing import Any

from kokoro.action import runtime as action_runtime
from kokoro.action import tool_spec
from kokoro.action import tools
from kokoro.action.tools import search_web as search_web_tool


def create_life_action_runtime(
    *,
    session: Any,
    section: dict[str, Any] | None = None,
    search_section: dict[str, Any] | None = None,
    registry: tool_spec.ActionToolRegistry | None = None,
) -> action_runtime.ActionRuntime:
    section = dict(section or {})
    search_section = dict(search_section or {})
    if registry is None:
        registry = tool_spec.ActionToolRegistry()
        tools.register_all(registry)
    search_client = search_web_tool.create_client(search_section)
    merge_window = section.get("result_merge_window_seconds", 1.0)
    if merge_window is None:
        merge_window = 1.0
    tool_timeout = section.get("tool_timeout", 45.0)
    if tool_timeout is None:
        tool_timeout = 45.0
    return action_runtime.ActionRuntime(
        session=session,
        handlers={},
        registry=registry,
        tool_context={
            "tool_timeout": float(tool_timeout),
            "character_id": getattr(session, "character_id", "default"),
            "memory_system": getattr(session, "memory_system", None),
            "memory_backend": getattr(session, "memory_backend", None),
            "web_search_client": search_client,
            "search_max_results": int(search_section.get("max_results", 5) or 5),
            "search_max_event_chars": int(search_section.get("max_event_chars", 6000) or 6000),
        },
        merge_window_seconds=float(merge_window),
    )


def attach_runtime_resources(
    *,
    life_runtime: Any,
    say_resources: Any | None = None,
) -> None:
    """Expose already-created transport/output resources to LifeRuntime tools."""

    runtime = getattr(life_runtime, "action_runtime", None)
    if runtime is None or not hasattr(runtime, "tool_context"):
        return
    if say_resources is None:
        return
    runtime.tool_context.update(
        {
            "say_resources": say_resources,
            "memory_backend": getattr(say_resources, "memory_backend", runtime.tool_context.get("memory_backend", None)),
            "character_id": getattr(getattr(say_resources, "session", None), "character_id", runtime.tool_context.get("character_id", "default")),
            "vts_controller": getattr(say_resources, "vts_controller", None),
            "vts_arbiter": getattr(say_resources, "vts_arbiter", None),
            "vts_body_driver": getattr(say_resources, "vts_body_driver", None),
            "event_loop": getattr(say_resources, "event_loop", None),
            "task_manager": getattr(say_resources, "task_manager", None),
            "qq_send_message": getattr(say_resources, "qq_send_message", None),
        }
    )
    attach = getattr(life_runtime, "attach_action_runtime", None)
    if callable(attach):
        attach(runtime)
