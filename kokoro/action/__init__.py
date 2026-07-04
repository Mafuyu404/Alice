"""Action capability model and runtime."""

CORE_MODULE_NAMES: set[str] = {
    "__init__",
    "action_policy",
    "agent_guard",
    "agent_loop",
    "autonomous_step",
    "cli_common",
    "cli_runtime",
    "dialogue_orchestrator",
    "input_sources",
    "model",
    "multi_chat",
    "multi_cli_runtime",
    "plan",
    "runtime",
    "single_cli_runtime",
    "tool_handlers",
    "tool_parser",
    "tool_registry",
    "tool_schemas",
    "tool_spec",
}

COMPAT_FACADE_MODULE_NAMES: set[str] = set()

from .model import (
    Action,
    ActionBatch,
    new_action_id,
    new_causality_id,
    new_cycle_id,
)
from .plan import ActionPlan, ActionPlanNode, execute_action_plan
from .runtime import ActionHandler, ActionRuntime
from .tool_spec import (
    ActionToolRegistry,
    PreparedAction,
    ToolContext,
    ToolResult,
    ToolSpec,
)

__all__ = [
    "Action",
    "ActionBatch",
    "ActionHandler",
    "ActionPlan",
    "ActionPlanNode",
    "ActionToolRegistry",
    "ActionRuntime",
    "COMPAT_FACADE_MODULE_NAMES",
    "CORE_MODULE_NAMES",
    "PreparedAction",
    "ToolContext",
    "ToolResult",
    "ToolSpec",
    "execute_action_plan",
    "new_action_id",
    "new_causality_id",
    "new_cycle_id",
]
