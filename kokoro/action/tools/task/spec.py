"""Registration for background task action tools."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools.task import execute, prepare


CLAUDE_CODE_EXEC_SCHEMA = {
    "type": "function",
    "function": {
        "name": "claude_code_exec",
        "description": "Start a background coding or computer-operation task through the agent task manager.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Concrete task goal, including relevant paths, content, and constraints.",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory; defaults to the project root.",
                },
            },
            "required": ["task"],
        },
    },
}


CHECK_TASK_PROGRESS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_task_progress",
        "description": "Check the latest status and progress of one or more background tasks.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Optional task id; when omitted, returns all active tasks.",
                },
            },
            "required": [],
        },
    },
}


LIST_ACTIVE_TASKS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_active_tasks",
        "description": "List all currently active background tasks.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


CANCEL_TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "cancel_task",
        "description": "Cancel a running or pending background task.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task id to cancel.",
                },
            },
            "required": ["task_id"],
        },
    },
}


def register(registry: tool_spec.ActionToolRegistry) -> None:
    registry.register(
        tool_spec.ToolSpec(
            name="claude_code_exec",
            actions={"claude_code_exec"},
            prepare=prepare.prepare_claude_code,
            execute=execute.execute_claude_code,
            schema=CLAUDE_CODE_EXEC_SCHEMA,
            timeout_seconds=5.0,
            max_parallel=1,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
    registry.register(
        tool_spec.ToolSpec(
            name="check_task_progress",
            actions={"check_task_progress"},
            prepare=prepare.prepare_task_id,
            execute=execute.execute_check_progress,
            schema=CHECK_TASK_PROGRESS_SCHEMA,
            timeout_seconds=5.0,
            max_parallel=2,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
    registry.register(
        tool_spec.ToolSpec(
            name="list_active_tasks",
            actions={"list_active_tasks"},
            prepare=prepare.prepare_list_active,
            execute=execute.execute_list_active,
            schema=LIST_ACTIVE_TASKS_SCHEMA,
            timeout_seconds=5.0,
            max_parallel=2,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
    registry.register(
        tool_spec.ToolSpec(
            name="cancel_task",
            actions={"cancel_task"},
            prepare=prepare.prepare_task_id,
            execute=execute.execute_cancel_task,
            schema=CANCEL_TASK_SCHEMA,
            timeout_seconds=5.0,
            max_parallel=2,
            default_visibility="private",
            default_result_policy="feed_back",
        )
    )
