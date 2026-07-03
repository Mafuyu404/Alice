"""Action tool modules and shared registration helpers."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools import get_current_app
from kokoro.action.tools import get_current_time
from kokoro.action.tools import memory
from kokoro.action.tools import observe_screen
from kokoro.action.tools import retire_sticker
from kokoro.action.tools import say
from kokoro.action.tools import search_web
from kokoro.action.tools import send_qq_message
from kokoro.action.tools import task
from kokoro.action.tools import vts

TOOL_MODULES = (
    get_current_app,
    get_current_time,
    memory,
    observe_screen,
    retire_sticker,
    say,
    search_web,
    send_qq_message,
    task,
    vts,
)

RUNTIME_MODULE_NAMES: set[str] = {
    "background",
    "debug_input",
    "live",
    "multi_relay",
    "qq",
    "single_runtime",
    "speech_input",
    "text_cli",
}

TOOL_ACTIONS: set[str] = {
    "cancel_task",
    "check_task_progress",
    "claude_code_exec",
    "get_current_app",
    "get_current_time",
    "list_active_tasks",
    "look_at_screen",
    "observe_screen",
    "retire_sticker",
    "save_to_memory",
    "say",
    "say_precomputed",
    "search_memory",
    "search_web",
    "send_qq_message",
    "stay_silent",
    "vts_expression",
    "vts_motion",
    "wait",
    "write_conversation_memory",
}

DEFAULT_ENABLED_TOOL_ACTIONS: set[str] = {
    "cancel_task",
    "check_task_progress",
    "claude_code_exec",
    "get_current_app",
    "get_current_time",
    "list_active_tasks",
    "look_at_screen",
    "search_memory",
    "save_to_memory",
    "send_qq_message",
    "retire_sticker",
    "vts_expression",
    "vts_motion",
}


def register_all(registry: tool_spec.ActionToolRegistry) -> None:
    for module in TOOL_MODULES:
        module.register(registry)
