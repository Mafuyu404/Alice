"""Compatibility exports for OpenAI-compatible tool JSON schemas.

New tool schemas live in each tool module's ``spec.py``.  This module only
aggregates them for older imports and configuration code.
"""

from __future__ import annotations

from kokoro.action.tools.get_current_app.spec import GET_CURRENT_APP_SCHEMA as GET_CURRENT_APP
from kokoro.action.tools.get_current_time.spec import GET_CURRENT_TIME_SCHEMA as GET_CURRENT_TIME
from kokoro.action.tools.memory.spec import SAVE_TO_MEMORY_SCHEMA as SAVE_TO_MEMORY
from kokoro.action.tools.memory.spec import SEARCH_MEMORY_SCHEMA as SEARCH_MEMORY
from kokoro.action.tools.observe_screen.spec import LOOK_AT_SCREEN_SCHEMA as LOOK_AT_SCREEN
from kokoro.action.tools.retire_sticker.spec import RETIRE_STICKER_SCHEMA as RETIRE_STICKER
from kokoro.action.tools.search_web.spec import WEB_SEARCH_SCHEMA
from kokoro.action.tools.send_qq_message.spec import SEND_QQ_MESSAGE_SCHEMA as SEND_QQ_MESSAGE
from kokoro.action.tools.task.spec import CANCEL_TASK_SCHEMA as CANCEL_TASK
from kokoro.action.tools.task.spec import CHECK_TASK_PROGRESS_SCHEMA as CHECK_TASK_PROGRESS
from kokoro.action.tools.task.spec import CLAUDE_CODE_EXEC_SCHEMA as CLAUDE_CODE_EXEC
from kokoro.action.tools.task.spec import LIST_ACTIVE_TASKS_SCHEMA as LIST_ACTIVE_TASKS
from kokoro.action.tools.vts.spec import VTS_EXPRESSION_SCHEMA as VTS_EXPRESSION
from kokoro.action.tools.vts.spec import VTS_MOTION_SCHEMA as VTS_MOTION

SEARCH_WEB = WEB_SEARCH_SCHEMA

ALL_TOOLS: list[dict] = [
    LOOK_AT_SCREEN,
    SEARCH_MEMORY,
    GET_CURRENT_TIME,
    GET_CURRENT_APP,
    SAVE_TO_MEMORY,
    SEARCH_WEB,
    SEND_QQ_MESSAGE,
    RETIRE_STICKER,
    VTS_EXPRESSION,
    VTS_MOTION,
    CLAUDE_CODE_EXEC,
    CHECK_TASK_PROGRESS,
    LIST_ACTIVE_TASKS,
    CANCEL_TASK,
]

ALL_TOOLS_BY_NAME: dict[str, dict] = {
    tool["function"]["name"]: tool for tool in ALL_TOOLS
}

DEFAULT_ENABLED_TOOLS: set[str] = {
    "look_at_screen",
    "search_memory",
    "get_current_time",
    "get_current_app",
    "save_to_memory",
    "send_qq_message",
    "retire_sticker",
    "vts_expression",
    "vts_motion",
    "claude_code_exec",
    "check_task_progress",
    "list_active_tasks",
    "cancel_task",
}
