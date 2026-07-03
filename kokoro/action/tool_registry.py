"""OpenAI-compatible function-call registry backed by action ToolSpec modules."""

from __future__ import annotations

import concurrent.futures
import logging
from dataclasses import dataclass
from typing import Callable

from kokoro.action import agent_loop
from kokoro.action import model as action_model
from kokoro.action import tool_spec
from kokoro.action import tools
from kokoro.core import config as cfg

logger = logging.getLogger(__name__)

Handler = Callable[..., str]


@dataclass
class FunctionCallToolRuntime:
    enabled: bool
    registry: "ToolRegistry | None" = None
    agent_config: agent_loop.AgentConfig | None = None

    def shutdown(self) -> None:
        if self.registry is not None:
            self.registry.shutdown()


@dataclass
class CliToolingRuntime:
    task_manager: object
    function_runtime: FunctionCallToolRuntime

    @property
    def enabled(self) -> bool:
        return self.function_runtime.enabled

    @property
    def agent_config(self):
        return self.function_runtime.agent_config

    def shutdown(self) -> None:
        self.function_runtime.shutdown()


class ToolRegistry:
    def __init__(self, enabled_tools: set[str] | None = None, tool_timeout: float = 45.0):
        if enabled_tools is None:
            enabled_tools = tools.DEFAULT_ENABLED_TOOL_ACTIONS.copy()
        self._enabled: set[str] = set(enabled_tools)
        self._timeout = tool_timeout
        self._handlers: dict[str, Handler] = {}
        self._schemas: dict[str, dict] = {}
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self._action_tools = tool_spec.ActionToolRegistry()
        tools.register_all(self._action_tools)
        for action_name in sorted(tools.TOOL_ACTIONS):
            self.register_tool_spec(action_name)

    def register(self, name: str, schema: dict, handler: Handler) -> None:
        self._schemas[name] = schema
        self._handlers[name] = handler

    def register_tool_spec(self, action_name: str, *, schema_name: str | None = None) -> None:
        spec = self._action_tools.resolve(action_name)
        if spec is None:
            raise KeyError(action_name)
        public_name = schema_name or action_name
        if spec.schema:
            self._schemas[public_name] = dict(spec.schema)

        def handler(arguments: dict, **context) -> str:
            action = action_model.Action(
                action=action_name,
                reason=f"function-call tool: {public_name}",
                args=dict(arguments or {}),
                mode="sync",
                visibility=spec.default_visibility,
                result_policy=spec.default_result_policy,
            )
            ctx = tool_spec.ToolContext(
                session=context.get("session"),
                data={**context, "tool_timeout": context.get("tool_timeout", self._timeout)},
            )
            result = self._action_tools.execute(ctx, action)
            return result.content

        self._handlers[public_name] = handler

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
            return f"tool '{name}' is not enabled or not registered"

        context.setdefault("tool_timeout", self._timeout)
        future = self._executor.submit(handler, arguments, **context)
        try:
            return future.result(timeout=self._timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("tool '%s' timed out after %.0fs", name, self._timeout)
            return f"tool '{name}' timed out after {self._timeout} seconds"
        except Exception as exc:
            logger.exception("tool '%s' failed", name)
            return f"tool '{name}' failed: {exc}"

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)
        self._action_tools.shutdown()


def create_registry(
    tool_list: list[str] | None = None,
    tool_timeout: float = 45.0,
) -> ToolRegistry:
    if tool_list is None:
        enabled = tools.DEFAULT_ENABLED_TOOL_ACTIONS.copy()
    else:
        known_tools = tools.TOOL_ACTIONS
        enabled = {name for name in tool_list if name in known_tools}
    return ToolRegistry(enabled_tools=enabled, tool_timeout=tool_timeout)


def create_function_call_runtime(
    *,
    disabled: bool,
    subtitle_client=None,
    printer=print,
) -> FunctionCallToolRuntime:
    enabled = cfg.tool_enabled() and not disabled
    if not enabled:
        return FunctionCallToolRuntime(enabled=False)

    tool_list = cfg.tool_list()
    tool_timeout = cfg.tool_timeout()
    max_iter = cfg.tool_max_iterations()
    registry = create_registry(
        tool_list=tool_list,
        tool_timeout=tool_timeout,
    )
    tool_schemas = registry.enabled_schemas()
    if not tool_schemas:
        printer("  [tool] No tools enabled (check config or model compatibility)")
        return FunctionCallToolRuntime(enabled=False, registry=registry)

    agent_config = agent_loop.AgentConfig(
        tools=tool_schemas,
        tool_registry=registry,
        max_tool_iterations=max_iter,
        tool_timeout=tool_timeout,
        subtitle_client=subtitle_client,
    )
    printer(f"  [tool] Enabled: {', '.join(s['function']['name'] for s in tool_schemas)}")
    return FunctionCallToolRuntime(
        enabled=True,
        registry=registry,
        agent_config=agent_config,
    )


def create_cli_tooling_runtime(
    *,
    disabled: bool,
    subtitle_client=None,
    printer=print,
) -> CliToolingRuntime:
    from kokoro.action.tools import task as task_tool

    return CliToolingRuntime(
        task_manager=task_tool.create_manager(),
        function_runtime=create_function_call_runtime(
            disabled=disabled,
            subtitle_client=subtitle_client,
            printer=printer,
        ),
    )
