"""Preparation stage for background task tools."""

from __future__ import annotations

from kokoro.action import model as action_model
from kokoro.action import tool_spec


def prepare_claude_code(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    args = dict(action.args)
    task = str(args.get("task") or args.get("goal") or args.get("description") or "").strip()
    working_dir = str(args.get("working_dir") or args.get("cwd") or "").strip()
    args.update({"task": task, "working_dir": working_dir})
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=action.reason or "prepare background task",
        metadata={"task_chars": len(task), "working_dir": working_dir},
    )


def prepare_task_id(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    args = dict(action.args)
    task_id = str(args.get("task_id") or args.get("id") or "").strip()
    args["task_id"] = task_id
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=action.reason or f"prepare {action.action}",
        metadata={"task_id": task_id},
    )


def prepare_list_active(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    return tool_spec.PreparedAction(
        action=action,
        args=dict(action.args),
        reason=action.reason or "prepare list active tasks",
        metadata={},
    )
