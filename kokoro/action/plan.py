"""LLM-produced action graph support.

This module validates structure and runs dependencies. It does not decide
whether a plan is reasonable; that judgment belongs in prompts/LLM output.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from typing import Any

from kokoro.action import model as action_model


@dataclass(frozen=True)
class ActionPlanNode:
    id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    after: list[str] = field(default_factory=list)
    parallel: bool = False
    reason: str = ""
    mode: str = "sync"
    visibility: str = "private"
    result_policy: str = "feed_back"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActionPlanNode":
        node_id = str(data.get("id") or data.get("action_id") or action_model.new_action_id()).strip()
        tool = str(data.get("tool") or data.get("action") or "").strip()
        if not tool:
            raise ValueError("action plan node must include tool/action")
        args = data.get("args")
        if not isinstance(args, dict):
            args = {}
        args = _normalize_node_args(data, args)
        after = data.get("after")
        if isinstance(after, str):
            after = [after]
        if not isinstance(after, list):
            after = []
        return cls(
            id=node_id,
            tool=tool,
            args=args,
            after=[str(item).strip() for item in after if str(item).strip()],
            parallel=bool(data.get("parallel", False)),
            reason=str(data.get("reason") or "").strip(),
            mode=str(data.get("mode") or "sync").strip() or "sync",
            visibility=str(data.get("visibility") or "private").strip() or "private",
            result_policy=str(data.get("result_policy") or "feed_back").strip() or "feed_back",
        )

    def to_action(self) -> action_model.Action:
        return action_model.Action(
            action=self.tool,
            reason=self.reason,
            args=dict(self.args),
            mode=self.mode if self.mode in {"sync", "async"} else "sync",
            visibility=self.visibility if self.visibility in {"public", "private", "silent"} else "private",
            result_policy=self.result_policy if self.result_policy in {"feed_back", "record_only", "trigger_next_step"} else "feed_back",
            action_id=self.id,
        )


@dataclass(frozen=True)
class ActionPlan:
    nodes: list[ActionPlanNode]
    reason: str = ""
    plan_id: str = ""
    causality_id: str = field(default_factory=action_model.new_causality_id)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActionPlan":
        raw_nodes = data.get("actions") or data.get("nodes") or []
        if isinstance(raw_nodes, dict):
            raw_nodes = [raw_nodes]
        if not isinstance(raw_nodes, list):
            raw_nodes = []
        nodes = [ActionPlanNode.from_dict(item) for item in raw_nodes if isinstance(item, dict)]
        plan = cls(
            nodes=nodes,
            reason=str(data.get("reason") or "").strip(),
            plan_id=str(data.get("plan_id") or data.get("id") or action_model.new_cycle_id()).strip(),
            causality_id=str(data.get("causality_id") or action_model.new_causality_id()).strip(),
        )
        plan.validate()
        return plan

    def validate(self) -> None:
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("action plan node ids must be unique")
        known = set(ids)
        for node in self.nodes:
            missing = [dep for dep in node.after if dep not in known]
            if missing:
                raise ValueError(f"unknown action dependencies for {node.id}: {missing}")
        _levels(self.nodes)

    def execution_levels(self) -> list[list[ActionPlanNode]]:
        return _levels(self.nodes)


def execute_action_plan(plan: ActionPlan, runtime) -> dict[str, str]:
    """Execute a plan dependency level by dependency level."""

    results: dict[str, str] = {}
    for level in plan.execution_levels():
        batch = action_model.ActionBatch(
            actions=[node.to_action() for node in level],
            reason=plan.reason,
            cycle_id=plan.plan_id,
            causality_id=plan.causality_id,
        )
        pairs = list(zip(level, batch.actions))
        if len(pairs) > 1 and any(node.parallel for node, _ in pairs):
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(pairs)) as executor:
                futures = {
                    executor.submit(runtime.execute_action_for_result, batch, action): node.id
                    for node, action in pairs
                }
                for future in concurrent.futures.as_completed(futures):
                    results[futures[future]] = future.result()
        else:
            for node, action in pairs:
                result = runtime.execute_action_for_result(batch, action)
                results[node.id] = result
    return results


def _levels(nodes: list[ActionPlanNode]) -> list[list[ActionPlanNode]]:
    remaining = {node.id: node for node in nodes}
    completed: set[str] = set()
    levels: list[list[ActionPlanNode]] = []
    while remaining:
        ready = [
            node
            for node in remaining.values()
            if all(dep in completed for dep in node.after)
        ]
        if not ready:
            cycle = ", ".join(sorted(remaining))
            raise ValueError(f"action plan has cyclic dependencies: {cycle}")
        ready.sort(key=lambda node: node.id)
        levels.append(ready)
        for node in ready:
            completed.add(node.id)
            remaining.pop(node.id, None)
    return levels


def _normalize_node_args(data: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Accept protocol-equivalent LLM output without changing the chosen tool."""

    normalized = dict(args)
    protocol_fields = {
        "id",
        "action_id",
        "tool",
        "action",
        "after",
        "parallel",
        "reason",
        "mode",
        "visibility",
        "result_policy",
    }
    for key, value in data.items():
        if key in protocol_fields or key == "args":
            continue
        if key not in normalized and value not in (None, "", [], {}):
            normalized[key] = value
    return normalized
