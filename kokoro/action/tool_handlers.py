"""Compatibility facade for legacy tool handler imports."""

from __future__ import annotations


def handle_get_current_time(arguments: dict, **context) -> str:
    return _execute_action_tool("get_current_time", arguments, context)


def handle_get_current_app(arguments: dict, **context) -> str:
    return _execute_action_tool("get_current_app", arguments, context)


def handle_search_memory(arguments: dict, **context) -> str:
    return _execute_action_tool("search_memory", arguments, context)


def handle_look_at_screen(arguments: dict, **context) -> str:
    return _execute_action_tool("look_at_screen", arguments, context)


def handle_save_to_memory(arguments: dict, **context) -> str:
    return _execute_action_tool("save_to_memory", arguments, context)


def handle_send_qq_message(arguments: dict, **context) -> str:
    return _execute_action_tool("send_qq_message", arguments, context)


def handle_retire_sticker(arguments: dict, **context) -> str:
    return _execute_action_tool("retire_sticker", arguments, context)


def handle_vts_expression(arguments: dict, **context) -> str:
    return _execute_action_tool("vts_expression", arguments, context)


def handle_vts_motion(arguments: dict, **context) -> str:
    return _execute_action_tool("vts_motion", arguments, context)


def handle_claude_code_exec(arguments: dict, **context) -> str:
    return _execute_action_tool("claude_code_exec", arguments, context)


def handle_check_task_progress(arguments: dict, **context) -> str:
    return _execute_action_tool("check_task_progress", arguments, context)


def handle_list_active_tasks(arguments: dict, **context) -> str:
    return _execute_action_tool("list_active_tasks", arguments, context)


def handle_cancel_task(arguments: dict, **context) -> str:
    return _execute_action_tool("cancel_task", arguments, context)


def _execute_action_tool(action_name: str, arguments: dict, context: dict) -> str:
    from kokoro.action import model as action_model
    from kokoro.action import tools
    from kokoro.action import tool_spec

    registry = tool_spec.ActionToolRegistry()
    tools.register_all(registry)
    try:
        action = action_model.Action(
            action=action_name,
            reason="legacy tool handler compatibility",
            args=dict(arguments or {}),
        )
        ctx = tool_spec.ToolContext(
            session=context.get("session"),
            data=dict(context or {}),
        )
        return registry.execute(ctx, action).content
    finally:
        registry.shutdown()
