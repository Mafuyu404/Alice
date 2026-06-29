"""Shared action batch model for inner-stream driven behavior."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def new_cycle_id() -> str:
    return f"cycle_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def new_action_id() -> str:
    return f"act_{uuid.uuid4().hex[:8]}"


def new_causality_id() -> str:
    return f"cause_{uuid.uuid4().hex[:10]}"


@dataclass(frozen=True)
class Action:
    action: str
    reason: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    mode: str = "sync"
    visibility: str = "private"
    result_policy: str = "feed_back"
    action_id: str = field(default_factory=new_action_id)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, fallback_action: str = "wait") -> "Action":
        action = str(data.get("action") or data.get("type") or fallback_action).strip().lower() or fallback_action
        args = data.get("args")
        if not isinstance(args, dict):
            args = {}
        return cls(
            action=action,
            reason=str(data.get("reason") or "").strip(),
            args=args,
            mode=_norm(data.get("mode"), {"sync", "async"}, "sync"),
            visibility=_norm(data.get("visibility"), {"public", "private", "silent"}, "private"),
            result_policy=_norm(
                data.get("result_policy"),
                {"feed_back", "record_only", "trigger_next_step"},
                "feed_back",
            ),
            action_id=str(data.get("action_id") or "").strip() or new_action_id(),
        )

    def with_defaults(
        self,
        *,
        mode: str | None = None,
        visibility: str | None = None,
        result_policy: str | None = None,
    ) -> "Action":
        return Action(
            action=self.action,
            reason=self.reason,
            args=dict(self.args),
            mode=mode or self.mode,
            visibility=visibility or self.visibility,
            result_policy=result_policy or self.result_policy,
            action_id=self.action_id,
        )


@dataclass(frozen=True)
class ActionBatch:
    actions: list[Action]
    reason: str = ""
    cycle_id: str = field(default_factory=new_cycle_id)
    causality_id: str = field(default_factory=new_causality_id)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, allowed_actions: set[str] | None = None) -> "ActionBatch":
        raw_actions = data.get("actions")
        if not isinstance(raw_actions, list):
            raw_actions = [data]
        actions = [Action.from_dict(item) for item in raw_actions if isinstance(item, dict)]
        if allowed_actions is not None:
            actions = [action for action in actions if action.action in allowed_actions]
        return cls(
            actions=actions,
            reason=str(data.get("reason") or "").strip(),
            cycle_id=str(data.get("cycle_id") or "").strip() or new_cycle_id(),
            causality_id=str(data.get("causality_id") or "").strip() or new_causality_id(),
        )

    def limited(self, *, max_actions: int = 3, max_public: int = 1) -> "ActionBatch":
        kept: list[Action] = []
        public_count = 0
        for action in self.actions:
            if len(kept) >= max_actions:
                break
            if action.visibility == "public":
                if public_count >= max_public:
                    continue
                public_count += 1
            kept.append(action)
        return ActionBatch(
            actions=kept,
            reason=self.reason,
            cycle_id=self.cycle_id,
            causality_id=self.causality_id,
        )


def _norm(value: object, allowed: set[str], fallback: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else fallback
