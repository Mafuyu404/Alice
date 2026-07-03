"""Action tool lifecycle contracts.

An action tool is a capability module, not just a function.  Tools may prepare
their executable arguments, execute the capability, and run follow-up behavior
after execution.  ActionRuntime owns the lifecycle and event feedback.
"""

from __future__ import annotations

import concurrent.futures
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from kokoro.action import model as action_model
from kokoro.core import lifecycle_debug

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedAction:
    action: action_model.Action
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    content: str
    status: str = "success"
    metadata: dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"


@dataclass
class ToolContext:
    session: Any
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "session":
            return self.session
        return self.data.get(key, default)


PrepareFn = Callable[[ToolContext, action_model.Action], PreparedAction]
ExecuteFn = Callable[[ToolContext, PreparedAction], ToolResult | str]
AfterFn = Callable[[ToolContext, PreparedAction, ToolResult], None]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    actions: set[str]
    execute: ExecuteFn
    prepare: PrepareFn | None = None
    after: AfterFn | None = None
    schema: dict[str, Any] | None = None
    timeout_seconds: float = 45.0
    max_parallel: int = 4
    default_visibility: str = "private"
    default_result_policy: str = "feed_back"

    def handles(self, action_name: str) -> bool:
        return action_name in self.actions


class ActionToolRegistry:
    """Registry for ToolSpec modules with prepare/execute/after lifecycle."""

    def __init__(self) -> None:
        self._by_action: dict[str, ToolSpec] = {}
        self._schemas: dict[str, dict[str, Any]] = {}
        self._executors: dict[str, concurrent.futures.ThreadPoolExecutor] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.name:
            raise ValueError("tool name cannot be empty")
        if not spec.actions:
            raise ValueError(f"tool {spec.name!r} must handle at least one action")
        for action in spec.actions:
            key = str(action or "").strip()
            if not key:
                raise ValueError(f"tool {spec.name!r} has empty action name")
            self._by_action[key] = spec
        if spec.schema:
            self._schemas[spec.name] = dict(spec.schema)
        self._executors[spec.name] = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, int(spec.max_parallel)),
            thread_name_prefix=f"tool-{spec.name}",
        )
        lifecycle_debug.log(
            "tool_registry.register",
            tool=spec.name,
            actions=sorted(spec.actions),
            has_prepare=spec.prepare is not None,
            has_after=spec.after is not None,
            timeout_seconds=spec.timeout_seconds,
            max_parallel=spec.max_parallel,
        )

    def resolve(self, action_name: str) -> ToolSpec | None:
        return self._by_action.get(action_name)

    def registered_actions(self) -> set[str]:
        return set(self._by_action)

    def enabled_schemas(self) -> list[dict[str, Any]]:
        return list(self._schemas.values())

    def execute(self, ctx: ToolContext, action: action_model.Action) -> ToolResult:
        spec = self.resolve(action.action)
        if spec is None:
            lifecycle_debug.log("tool_registry.execute.unregistered", action=action)
            return ToolResult(content=f"action is not registered: {action.action}", status="failed")
        lifecycle_debug.log("tool_registry.execute.start", tool=spec.name, action=action, context=ctx.data)
        prepared = self.prepare(ctx, action, spec=spec)
        return self.execute_prepared(ctx, prepared, spec=spec)

    def prepare(
        self,
        ctx: ToolContext,
        action: action_model.Action,
        *,
        spec: ToolSpec | None = None,
    ) -> PreparedAction:
        spec = spec or self.resolve(action.action)
        if spec is None or spec.prepare is None:
            prepared = PreparedAction(action=action, args=dict(action.args), reason=action.reason)
            lifecycle_debug.log(
                "tool_registry.prepare.default",
                tool=getattr(spec, "name", ""),
                action=action,
                prepared=prepared,
            )
            return prepared
        lifecycle_debug.log("tool_registry.prepare.start", tool=spec.name, action=action, context=ctx.data)
        prepared = spec.prepare(ctx, action)
        lifecycle_debug.log("tool_registry.prepare.done", tool=spec.name, action=action, prepared=prepared)
        return prepared

    def execute_prepared(
        self,
        ctx: ToolContext,
        prepared: PreparedAction,
        *,
        spec: ToolSpec | None = None,
    ) -> ToolResult:
        spec = spec or self.resolve(prepared.action.action)
        if spec is None:
            lifecycle_debug.log("tool_registry.execute_prepared.unregistered", prepared=prepared)
            return ToolResult(content=f"action is not registered: {prepared.action.action}", status="failed")
        executor = self._executors[spec.name]
        lifecycle_debug.log("tool_registry.execute_prepared.start", tool=spec.name, prepared=prepared)
        try:
            future = executor.submit(spec.execute, ctx, prepared)
        except RuntimeError as exc:
            lifecycle_debug.log(
                "tool_registry.execute_prepared.submit_error",
                tool=spec.name,
                prepared=prepared,
                error=str(exc),
            )
            return ToolResult(content=f"tool could not start: {spec.name}: {exc}", status="failed")
        try:
            raw = future.result(timeout=max(0.1, float(spec.timeout_seconds)))
            result = raw if isinstance(raw, ToolResult) else ToolResult(content=str(raw or ""))
            lifecycle_debug.log("tool_registry.execute_prepared.result", tool=spec.name, prepared=prepared, result=result)
        except concurrent.futures.TimeoutError:
            logger.warning("tool '%s' timed out after %.0fs", spec.name, spec.timeout_seconds)
            result = ToolResult(content=f"tool timed out: {spec.name}", status="failed")
            lifecycle_debug.log("tool_registry.execute_prepared.timeout", tool=spec.name, prepared=prepared)
        except Exception as exc:
            logger.exception("tool '%s' failed", spec.name)
            result = ToolResult(content=f"tool failed: {spec.name}: {type(exc).__name__}: {exc}", status="failed")
            lifecycle_debug.log(
                "tool_registry.execute_prepared.error",
                tool=spec.name,
                prepared=prepared,
                error=str(exc),
                result=result,
            )
        if spec.after is not None:
            try:
                lifecycle_debug.log("tool_registry.after.start", tool=spec.name, prepared=prepared, result=result)
                spec.after(ctx, prepared, result)
                lifecycle_debug.log("tool_registry.after.done", tool=spec.name, prepared=prepared, result=result)
            except Exception:
                logger.exception("tool '%s' after hook failed", spec.name)
                lifecycle_debug.log("tool_registry.after.error", tool=spec.name, prepared=prepared, result=result)
        return result

    def shutdown(self) -> None:
        for executor in self._executors.values():
            executor.shutdown(wait=False)
