"""Tool registry: maps tool names to schemas and handler functions."""

from __future__ import annotations

import concurrent.futures
import logging
from typing import Callable

from kokoro import tool_handlers
from kokoro import tool_schemas

logger = logging.getLogger(__name__)

Handler = Callable[..., str]

_BUILTIN_HANDLERS: dict[str, Handler] = {
    "look_at_screen": tool_handlers.handle_look_at_screen,
    "search_memory": tool_handlers.handle_search_memory,
    "get_current_time": tool_handlers.handle_get_current_time,
    "get_current_app": tool_handlers.handle_get_current_app,
    "save_to_memory": tool_handlers.handle_save_to_memory,
    "vts_expression": tool_handlers.handle_vts_expression,
    "claude_code_exec": tool_handlers.handle_claude_code_exec,
    "check_task_progress": tool_handlers.handle_check_task_progress,
    "list_active_tasks": tool_handlers.handle_list_active_tasks,
    "cancel_task": tool_handlers.handle_cancel_task,
}


class ToolRegistry:
    def __init__(self, enabled_tools: set[str] | None = None, tool_timeout: float = 45.0):
        if enabled_tools is None:
            enabled_tools = tool_schemas.DEFAULT_ENABLED_TOOLS.copy()
        self._enabled: set[str] = set(enabled_tools)
        self._timeout = tool_timeout
        self._handlers: dict[str, Handler] = {}
        self._schemas: dict[str, dict] = {}
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        for name, handler in _BUILTIN_HANDLERS.items():
            self.register(name, tool_schemas.ALL_TOOLS_BY_NAME[name], handler)

    def register(self, name: str, schema: dict, handler: Handler) -> None:
        self._schemas[name] = schema
        self._handlers[name] = handler

    def is_enabled(self, name: str) -> bool:
        return name in self._enabled

    def get_schema(self, name: str) -> dict | None:
        if name not in self._enabled:
            return None
        return self._schemas.get(name)

    def get_handler(self, name: str) -> Handler | None:
        if name not in self._enabled:
            return None
        return self._handlers.get(name)

    def enabled_schemas(self) -> list[dict]:
        return [
            self._schemas[name]
            for name in self._enabled
            if name in self._schemas
        ]

    def execute(self, name: str, arguments: dict, **context) -> str:
        handler = self.get_handler(name)
        if handler is None:
            return f"工具 '{name}' 未启用或未注册。"

        context.setdefault("tool_timeout", self._timeout)
        future = self._executor.submit(handler, arguments, **context)
        try:
            return future.result(timeout=self._timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("tool '%s' timed out after %.0fs", name, self._timeout)
            return f"工具 '{name}' 执行超时（{self._timeout}秒）。"
        except Exception as exc:
            logger.exception("tool '%s' failed", name)
            return f"工具 '{name}' 执行失败：{exc}"

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)


def create_registry(
    tool_list: list[str] | None = None,
    tool_timeout: float = 45.0,
) -> ToolRegistry:
    if tool_list is None:
        enabled = tool_schemas.DEFAULT_ENABLED_TOOLS.copy()
    else:
        enabled = {name for name in tool_list if name in _BUILTIN_HANDLERS}
    return ToolRegistry(enabled_tools=enabled, tool_timeout=tool_timeout)
