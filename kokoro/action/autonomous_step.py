"""Unified autonomous action decision and execution.

This module keeps the AI as the decision maker.  Runtime code supplies the
recent reality, available capabilities, and hard execution boundaries; the LLM
chooses one next action.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from kokoro.core import config as cfg
from kokoro.core import deepseek_api
from kokoro.core import prompts
from kokoro.action import model as action_model
from kokoro.action import runtime as action_runtime
from kokoro.action import tool_spec
from kokoro.action.tools import observe_screen
from kokoro.action.tools import qq as qq_tool
from kokoro.action.tools import search_web as search_web_tool
from kokoro.core import input_events
from kokoro.core import lifecycle_debug

logger = logging.getLogger(__name__)

_WAIT_NOTICE_THRESHOLDS_SECONDS = (30, 90, 300, 900)


@dataclass(frozen=True)
class AutonomousDecision:
    action: str = "wait"
    reason: str = ""
    conversation_id: str = ""
    message: str = ""
    query: str = ""
    memory_note: str = ""
    cognition_note: str = ""
    target: str = ""
    tags: list[str] = field(default_factory=list)
    sticker_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutonomousDecision":
        action = str(data.get("action") or "wait").strip().lower()
        if action not in {"wait", "observe", "observe_screen", "say_qq", "send_sticker", "search_web", "remember", "update_cognition"}:
            action = "wait"
        tags = data.get("tags")
        if not isinstance(tags, list):
            tags = []
        return cls(
            action=action,
            reason=str(data.get("reason") or "").strip(),
            conversation_id=str(data.get("conversation_id") or "").strip(),
            message=str(data.get("message") or "").strip(),
            query=str(data.get("query") or "").strip(),
            memory_note=str(data.get("memory_note") or data.get("event") or "").strip(),
            cognition_note=str(data.get("cognition_note") or data.get("cognition") or "").strip(),
            target=str(data.get("target") or "").strip(),
            tags=[str(tag).strip() for tag in tags if str(tag).strip()][:6],
            sticker_id=str(data.get("sticker_id") or "").strip(),
        )


_ACTION_ALIASES = {
    "observe": "observe_screen",
    "remember": "write_memory",
    "say_qq": "say_qq",
}


class AutonomousStep:
    """Single AI-driven action loop for idle/QQ/search/memory/cognition choices."""

    def __init__(
        self,
        *,
        session,
        section: dict[str, Any] | None = None,
        search_section: dict[str, Any] | None = None,
    ) -> None:
        section = dict(section or {})
        search_section = dict(search_section or {})
        self.session = session
        self.enabled = bool(section.get("enabled", True))
        self.model = str(section.get("model") or "").strip() or cfg.dialogue_model() or cfg.llm_model()
        self.max_tokens = int(section.get("max_tokens", 512) or 512)
        self.min_interval_seconds = max(0.0, float(section.get("min_interval_seconds", 3.0) or 3.0))
        self.search_client = search_web_tool.create_client(search_section)
        self.search_max_results = int(search_section.get("max_results", 5) or 5)
        self.search_max_event_chars = int(search_section.get("max_event_chars", 6000) or 6000)
        self._last_decide_at_by_scope: dict[str, float] = {}
        self._last_say_at_by_conversation: dict[str, float] = {}
        self._wait_started_at: float = 0.0
        self._last_wait_at: float = 0.0
        self._wait_count: int = 0
        self._last_wait_reason: str = ""
        self._last_wait_notice_threshold: int = 0
        self._sticker_provider = None
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._memory_reflector = None
        self._cognition_reflector = None
        self._tool_registry = tool_spec.ActionToolRegistry()
        observe_screen.register(self._tool_registry)
        search_web_tool.register(self._tool_registry)
        self._runtime = action_runtime.ActionRuntime(
            session=session,
            handlers={
                "wait": self._action_wait,
                "observe": self._action_wait,
                "write_memory": self._action_write_memory,
                "remember": self._action_write_memory,
                "update_cognition": self._action_update_cognition,
            },
            registry=self._tool_registry,
            tool_context={
                "tool_timeout": 45,
                "web_search_client": self.search_client,
                "search_max_results": self.search_max_results,
                "search_max_event_chars": self.search_max_event_chars,
            },
            merge_window_seconds=float(section.get("result_merge_window_seconds", 1.0) or 1.0),
        )
        lifecycle_debug.log(
            "autonomous_step.init",
            character_id=getattr(session, "character_id", ""),
            enabled=self.enabled,
            model=self.model,
            min_interval_seconds=self.min_interval_seconds,
            registered_actions=sorted(self._tool_registry.registered_actions()),
        )

    def attach_reflectors(self, *, memory_reflector=None, cognition_reflector=None) -> None:
        self._memory_reflector = memory_reflector
        self._cognition_reflector = cognition_reflector

    def attach_sticker_provider(self, provider) -> None:
        self._sticker_provider = provider

    def mark_social_output(self, conversation_id: str) -> None:
        cid = str(conversation_id or "").strip()
        if cid:
            self._last_say_at_by_conversation[cid] = time.time()
        self.clear_wait_state(reason="social output")

    def clear_wait_state(self, *, reason: str = "") -> None:
        if not self._wait_started_at:
            return
        lifecycle_debug.log(
            "autonomous_step.wait_state.clear",
            reason=reason,
            elapsed_seconds=round(time.time() - self._wait_started_at, 1),
            wait_count=self._wait_count,
            last_wait_reason=self._last_wait_reason,
        )
        self._wait_started_at = 0.0
        self._last_wait_at = 0.0
        self._wait_count = 0
        self._last_wait_reason = ""
        self._last_wait_notice_threshold = 0

    def note_external_event(self, event: input_events.InputEvent) -> None:
        if event.type == "time_tick":
            return
        if event.type == "action_result" and event.metadata.get("action") == "wait":
            return
        if event.type == "self_action" and event.metadata.get("action") in {"wait", "wait_status"}:
            return
        self.clear_wait_state(reason=f"event:{event.type}:{event.source}")

    def activity_context(self) -> str:
        if not self._wait_started_at:
            return ""
        now = time.time()
        elapsed = max(0, int(now - self._wait_started_at))
        since_last = max(0, int(now - (self._last_wait_at or self._wait_started_at)))
        reason = self._last_wait_reason or "waiting"
        return (
            f"当前活动状态：我已经连续选择等待约 {elapsed} 秒；"
            f"最近一次等待距现在约 {since_last} 秒；"
            f"累计等待选择 {self._wait_count} 次；"
            f"最近一次等待理由：{reason}。"
            "这表示时间正在流逝，但没有新的外部输入必须立刻处理。"
        )

    def decide(
        self,
        *,
        events: list[input_events.InputEvent] | None = None,
        context: dict[str, Any] | None = None,
        qq_packets: list[Any] | None = None,
        trigger_reason: str = "",
        capabilities: list[str] | None = None,
        cooldown_scope: str = "",
    ) -> AutonomousDecision:
        if not self.enabled:
            lifecycle_debug.log("autonomous_step.decide.disabled")
            return AutonomousDecision(reason="autonomous step disabled")
        capabilities = capabilities or ["say_qq", "send_sticker", "search_web", "remember", "update_cognition", "observe", "wait"]
        scope = str(cooldown_scope or ("qq" if "say_qq" in capabilities else "background")).strip() or "default"
        now = time.monotonic()
        last_decide_at = self._last_decide_at_by_scope.get(scope, 0.0)
        if self.min_interval_seconds > 0 and now - last_decide_at < self.min_interval_seconds:
            lifecycle_debug.log(
                "autonomous_step.decide.cooldown",
                scope=scope,
                elapsed=now - last_decide_at,
                min_interval_seconds=self.min_interval_seconds,
            )
            return AutonomousDecision(reason="autonomous step cooldown")
        self._last_decide_at_by_scope[scope] = now
        context = self._enrich_context(
            events=events or [],
            context=context or {},
            qq_packets=qq_packets or [],
        )
        # Calculate silent streak for each active conversation
        silent_info = self._silent_streak_info(qq_packets or [])
        context["silent_streak"] = silent_info
        system = "\n\n".join(
            part
            for part in (
                prompts.skill("inner_continuity"),
                prompts.skill("social_presence"),
                prompts.skill("memory_cognition"),
                prompts.get("autonomous_step.system", ""),
            )
            if part
        )
        user = self._build_user_prompt(
            events=events or [],
            context=context,
            qq_packets=qq_packets or [],
            trigger_reason=trigger_reason,
            capabilities=capabilities,
        )
        try:
            lifecycle_debug.log(
                "autonomous_step.decide.llm.start",
                scope=scope,
                model=self.model,
                max_tokens=self.max_tokens,
                trigger_reason=trigger_reason,
                capabilities=capabilities,
                events=events or [],
                context=context,
                system_prompt=system,
                user_prompt=user,
            )
            raw = deepseek_api.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=self.model,
                temperature=0.25,
                max_tokens=self.max_tokens,
                json_mode=True,
                function="autonomous_step",
            )["content"]
            lifecycle_debug.log(
                "autonomous_step.decide.llm.result",
                scope=scope,
                raw_response=raw,
            )
            data = _extract_json_object(raw) or {}
            decision = AutonomousDecision.from_dict(data)
            sanitized = self._sanitize_decision(decision, qq_packets or [], capabilities)
            lifecycle_debug.log(
                "autonomous_step.decide.done",
                scope=scope,
                parsed=data,
                decision=decision,
                sanitized=sanitized,
            )
            return sanitized
        except Exception as exc:
            logger.warning("autonomous step decision failed: %s", exc)
            lifecycle_debug.log(
                "autonomous_step.decide.error",
                scope=scope,
                error=str(exc),
            )
            return AutonomousDecision(reason=type(exc).__name__)

    def decide_batch(
        self,
        *,
        events: list[input_events.InputEvent] | None = None,
        context: dict[str, Any] | None = None,
        qq_packets: list[Any] | None = None,
        trigger_reason: str = "",
        capabilities: list[str] | None = None,
        cooldown_scope: str = "",
    ) -> action_model.ActionBatch:
        if not self.enabled:
            lifecycle_debug.log("autonomous_step.batch.disabled")
            return action_model.ActionBatch(actions=[], reason="autonomous step disabled")
        capabilities = capabilities or ["observe_screen", "search_web", "write_memory", "update_cognition", "wait"]
        scope = str(cooldown_scope or ("qq" if "say_qq" in capabilities else "background")).strip() or "default"
        now = time.monotonic()
        last_decide_at = self._last_decide_at_by_scope.get(scope, 0.0)
        if self.min_interval_seconds > 0 and now - last_decide_at < self.min_interval_seconds:
            lifecycle_debug.log(
                "autonomous_step.batch.cooldown",
                scope=scope,
                elapsed=now - last_decide_at,
                min_interval_seconds=self.min_interval_seconds,
            )
            return action_model.ActionBatch(actions=[], reason="autonomous step cooldown")
        self._last_decide_at_by_scope[scope] = now

        context = self._enrich_context(
            events=events or [],
            context=context or {},
            qq_packets=qq_packets or [],
        )
        context["silent_streak"] = self._silent_streak_info(qq_packets or [])
        system = "\n\n".join(
            part
            for part in (
                prompts.skill("inner_continuity"),
                prompts.skill("social_presence"),
                prompts.skill("memory_cognition"),
                prompts.get("autonomous_step.batch_system", ""),
            )
            if part
        )
        user = self._build_user_prompt(
            events=events or [],
            context=context,
            qq_packets=qq_packets or [],
            trigger_reason=trigger_reason,
            capabilities=capabilities,
        )
        try:
            lifecycle_debug.log(
                "autonomous_step.batch.llm.start",
                scope=scope,
                model=self.model,
                max_tokens=self.max_tokens,
                trigger_reason=trigger_reason,
                capabilities=capabilities,
                events=events or [],
                context=context,
                system_prompt=system,
                user_prompt=user,
            )
            raw = deepseek_api.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=self.model,
                temperature=0.25,
                max_tokens=self.max_tokens,
                json_mode=True,
                function="autonomous_action_batch",
            )["content"]
            lifecycle_debug.log(
                "autonomous_step.batch.llm.result",
                scope=scope,
                raw_response=raw,
            )
            data = _extract_json_object(raw) or {}
            batch = action_model.ActionBatch.from_dict(data)
            sanitized = self._sanitize_batch(
                batch,
                qq_packets=qq_packets or [],
                capabilities=capabilities,
            )
            lifecycle_debug.log(
                "autonomous_step.batch.done",
                scope=scope,
                parsed=data,
                batch=batch,
                sanitized=sanitized,
            )
            return sanitized
        except Exception as exc:
            logger.warning("autonomous action batch failed: %s", exc)
            lifecycle_debug.log(
                "autonomous_step.batch.error",
                scope=scope,
                error=str(exc),
            )
            return action_model.ActionBatch(actions=[], reason=type(exc).__name__)

    def _enrich_context(
        self,
        *,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        qq_packets: list[Any],
    ) -> dict[str, Any]:
        enriched = dict(context or {})
        text_parts: list[str] = []
        text_parts.extend(event.visible_content() for event in events)
        for packet in qq_packets:
            text_parts.append(_format_packet(packet))
        query_text = "\n".join(part for part in text_parts if str(part or "").strip())[:4000]
        cognition = getattr(self.session, "cognition", None)
        if cognition is not None and hasattr(cognition, "get_context_for_text") and query_text:
            try:
                cognition_context = cognition.get_context_for_text(query_text)
                if cognition_context:
                    enriched["cognition_context"] = cognition_context
            except Exception:
                pass
        return enriched

    def execute(
        self,
        decision: AutonomousDecision,
        *,
        events: list[input_events.InputEvent] | None = None,
        context: dict[str, Any] | None = None,
        trigger_reason: str = "",
    ) -> None:
        if decision.action == "search_web" and decision.query:
            self._execute_search(decision)
        elif decision.action == "remember":
            self._execute_remember(decision, events=events or [], context=context or {}, trigger_reason=trigger_reason)
        elif decision.action == "update_cognition":
            self._execute_cognition(decision, events=events or [], context=context or {}, trigger_reason=trigger_reason)
        if decision.action in ("say_qq", "send_sticker") and decision.conversation_id:
            self._last_say_at_by_conversation[decision.conversation_id] = time.time()

    def execute_batch(
        self,
        batch: action_model.ActionBatch,
        *,
        events: list[input_events.InputEvent] | None = None,
        context: dict[str, Any] | None = None,
        trigger_reason: str = "",
    ) -> None:
        if not batch.actions:
            lifecycle_debug.log(
                "autonomous_step.execute_batch.empty",
                batch=batch,
                trigger_reason=trigger_reason,
            )
            return
        lifecycle_debug.log(
            "autonomous_step.execute_batch.start",
            batch=batch,
            events=events or [],
            context=context or {},
            trigger_reason=trigger_reason,
        )
        if any(action.action != "wait" for action in batch.actions):
            self.clear_wait_state(reason="non-wait action selected")
        self._runtime.execute_batch(batch)

    def execute_async(
        self,
        decision: AutonomousDecision,
        *,
        events: list[input_events.InputEvent] | None = None,
        context: dict[str, Any] | None = None,
        trigger_reason: str = "",
    ) -> None:
        thread = threading.Thread(
            target=self.execute,
            kwargs={
                "decision": decision,
                "events": list(events or []),
                "context": dict(context or {}),
                "trigger_reason": trigger_reason,
            },
            daemon=True,
        )
        self._threads.append(thread)
        thread.start()

    def consider_after_inner_stream(
        self,
        *,
        inner_stream: str,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str = "",
    ) -> None:
        if not events:
            lifecycle_debug.log("autonomous_step.after_inner_stream.no_events", trigger_reason=trigger_reason)
            return
        if not self._lock.acquire(blocking=False):
            lifecycle_debug.log("autonomous_step.after_inner_stream.lock_busy", trigger_reason=trigger_reason)
            return
        lifecycle_debug.log(
            "autonomous_step.after_inner_stream.thread_start",
            trigger_reason=trigger_reason,
            inner_stream=inner_stream,
            events=events,
            context=context,
        )
        thread = threading.Thread(
            target=self._run_after_inner_stream,
            kwargs={
                "inner_stream": inner_stream,
                "events": list(events),
                "context": dict(context or {}),
                "trigger_reason": trigger_reason,
            },
            daemon=True,
        )
        self._threads.append(thread)
        thread.start()

    def shutdown(self, *, wait: bool = True, timeout: float = 8.0) -> None:
        lifecycle_debug.log("autonomous_step.shutdown.start", wait=wait, timeout=timeout)
        if wait:
            deadline = time.monotonic() + max(0.0, timeout)
            for thread in list(self._threads):
                remaining = max(0.0, deadline - time.monotonic())
                if remaining <= 0:
                    break
                if thread.is_alive():
                    thread.join(timeout=remaining)
        shutdown = getattr(self._runtime, "shutdown", None)
        if callable(shutdown):
            shutdown(wait=wait, timeout=timeout)
        lifecycle_debug.log("autonomous_step.shutdown.done")

    def _run_after_inner_stream(
        self,
        *,
        inner_stream: str,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str,
    ) -> None:
        try:
            lifecycle_debug.log(
                "autonomous_step.after_inner_stream.run",
                trigger_reason=trigger_reason,
                inner_stream=inner_stream,
                events=events,
                context=context,
            )
            enriched = dict(context or {})
            enriched["inner_stream"] = inner_stream
            batch = self.decide_batch(
                events=events,
                context=enriched,
                trigger_reason=trigger_reason,
                capabilities=["search_web", "write_memory", "update_cognition", "wait", "observe_screen"],
                cooldown_scope="inner_stream",
            )
            self.execute_batch(batch, events=events, context=enriched, trigger_reason=trigger_reason)
        finally:
            self._lock.release()

    def _build_user_prompt(
        self,
        *,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        qq_packets: list[Any],
        trigger_reason: str,
        capabilities: list[str],
    ) -> str:
        packet_text = "\n\n---\n\n".join(_format_packet(packet) for packet in qq_packets) or "无"
        return prompts.format_prompt(
            "autonomous_step.user",
            name=getattr(self.session, "character_name", ""),
            user_name=getattr(self.session, "user_name", "你"),
            trigger_reason=trigger_reason or "自主节奏",
            capabilities=", ".join(capabilities),
            inner_stream=context.get("inner_stream") or _safe_context(getattr(self.session, "inner_stream", None)) or "无",
            cognition_context=context.get("cognition_context") or _safe_context(getattr(self.session, "cognition", None)) or "无",
            memory_context=context.get("memory_context") or "无",
            recent_history=context.get("recent_history") or "无",
            summary=context.get("summary") or "无",
            scene_context=context.get("scene_context") or "无",
            events=input_events.format_events_for_prompt(events, max_chars=3000) or "无",
            qq_packets=packet_text,
            silent_streak=context.get("silent_streak") or "",
            sticker_candidates=context.get("sticker_candidates") or "",
        )

    def _silent_streak_info(self, qq_packets: list[Any]) -> str:
        if not qq_packets:
            return ""
        now = time.time()
        info: list[str] = []
        for packet in qq_packets:
            cid = str(getattr(packet, "conversation_id", "") or "")
            last_say = self._last_say_at_by_conversation.get(cid)
            if last_say is not None:
                elapsed = max(0, int(now - last_say))
                info.append(f"{cid}: 已沉默 {elapsed} 秒")
        return "; ".join(info)

    def _sanitize_decision(
        self,
        decision: AutonomousDecision,
        qq_packets: list[Any],
        capabilities: list[str],
    ) -> AutonomousDecision:
        allowed = set(capabilities or [])
        if decision.action not in allowed and decision.action not in {"wait", "observe"}:
            return AutonomousDecision(reason=f"action not available: {decision.action}")
        if decision.action == "say_qq":
            valid_ids = {str(getattr(packet, "conversation_id", "") or "") for packet in qq_packets}
            if decision.conversation_id not in valid_ids:
                normalized = _resolve_qq_conversation_id(decision.conversation_id, qq_packets)
                if not normalized:
                    logger.info(
                        "autonomous step rejected unknown qq conversation: chosen=%r valid=%s",
                        decision.conversation_id,
                        sorted(valid_ids),
                    )
                    return AutonomousDecision(reason="unknown qq conversation")
                decision = AutonomousDecision(
                    action=decision.action,
                    reason=decision.reason,
                    conversation_id=normalized,
                    message=decision.message,
                    query=decision.query,
                    memory_note=decision.memory_note,
                    cognition_note=decision.cognition_note,
                    target=decision.target,
                    tags=decision.tags,
                )
            if not decision.message:
                return AutonomousDecision(reason="empty qq message")
        if decision.action == "send_sticker":
            valid_ids = {str(getattr(packet, "conversation_id", "") or "") for packet in qq_packets}
            if decision.conversation_id not in valid_ids:
                normalized = _resolve_qq_conversation_id(decision.conversation_id, qq_packets)
                if not normalized:
                    return AutonomousDecision(reason="unknown qq conversation for sticker")
                decision = AutonomousDecision(
                    action=decision.action, reason=decision.reason,
                    conversation_id=normalized, message=decision.message,
                    query=decision.query, memory_note=decision.memory_note,
                    cognition_note=decision.cognition_note, target=decision.target,
                    tags=decision.tags, sticker_id=decision.sticker_id,
                )
            if not decision.sticker_id and not decision.message:
                return AutonomousDecision(reason="empty sticker_id and message")
        if decision.action == "search_web" and not decision.query:
            return AutonomousDecision(reason="empty search query")
        if decision.action == "remember" and not decision.memory_note:
            return AutonomousDecision(reason="empty memory note")
        if decision.action == "update_cognition" and not decision.cognition_note:
            return AutonomousDecision(reason="empty cognition note")
        return decision

    def _sanitize_batch(
        self,
        batch: action_model.ActionBatch,
        *,
        qq_packets: list[Any],
        capabilities: list[str],
    ) -> action_model.ActionBatch:
        allowed = {_ACTION_ALIASES.get(name, name) for name in capabilities}
        actions: list[action_model.Action] = []
        for action in batch.actions:
            normalized = _ACTION_ALIASES.get(action.action, action.action)
            if normalized not in allowed and normalized not in {"wait"}:
                continue
            args = dict(action.args)
            if normalized == "search_web" and not str(args.get("query") or "").strip():
                continue
            if normalized == "write_memory" and not str(args.get("memory_note") or args.get("note") or "").strip():
                continue
            if normalized == "update_cognition" and not str(args.get("cognition_note") or args.get("note") or "").strip():
                continue
            if normalized == "say_qq" and not str(args.get("message") or args.get("text") or "").strip():
                continue
            if normalized == "send_sticker" and not str(args.get("sticker_id") or "").strip():
                continue
            if normalized in {"say_qq", "send_sticker"} and not str(args.get("conversation_id") or "").strip():
                normalized_id = _resolve_qq_conversation_id("", qq_packets)
                if normalized_id:
                    args["conversation_id"] = normalized_id
                else:
                    continue
            current = action_model.Action(
                action=normalized,
                reason=action.reason,
                args=args,
                mode="async" if normalized in {"observe_screen", "search_web", "write_memory", "update_cognition"} else action.mode,
                visibility="public" if normalized in {"say_qq", "send_sticker"} else action.visibility,
                result_policy=action.result_policy,
                action_id=action.action_id,
            )
            actions.append(self._normalize_wait_action(current))
        return action_model.ActionBatch(
            actions=actions,
            reason=batch.reason,
            cycle_id=batch.cycle_id,
            causality_id=batch.causality_id,
        ).limited(max_actions=3, max_public=1)

    def _normalize_wait_action(self, action: action_model.Action) -> action_model.Action:
        if action.action != "wait":
            return action
        args = dict(action.args)
        args.setdefault("suppress_feedback", True)
        return action_model.Action(
            action=action.action,
            reason=action.reason,
            args=args,
            mode=action.mode,
            visibility=action.visibility,
            result_policy="record_only",
            action_id=action.action_id,
        )

    def _action_wait(self, action: action_model.Action) -> str:
        reason = action.reason or str(action.args.get("reason") or "").strip() or "内在叙事流选择暂时不采取外部行动"
        return f"我选择暂时等待/旁听。原因：{reason}"

    def _action_wait(self, action: action_model.Action) -> str:
        reason = action.reason or str(action.args.get("reason") or "").strip() or "wait"
        now = time.time()
        if not self._wait_started_at:
            self._wait_started_at = now
            self._last_wait_notice_threshold = 0
        self._last_wait_at = now
        self._wait_count += 1
        self._last_wait_reason = reason
        elapsed = max(0, int(now - self._wait_started_at))
        self._maybe_publish_wait_notice(elapsed, reason)
        lifecycle_debug.log(
            "autonomous_step.wait_state.update",
            elapsed_seconds=elapsed,
            wait_count=self._wait_count,
            reason=reason,
        )
        return f"我选择暂时等待/旁听。已经连续等待约 {elapsed} 秒。原因：{reason}"

    def _maybe_publish_wait_notice(self, elapsed_seconds: int, reason: str) -> None:
        threshold = 0
        for candidate in _WAIT_NOTICE_THRESHOLDS_SECONDS:
            if elapsed_seconds >= candidate:
                threshold = candidate
        if threshold <= 0 or threshold <= self._last_wait_notice_threshold:
            return
        self._last_wait_notice_threshold = threshold
        record = getattr(self.session, "record_self_action", None)
        if not callable(record):
            return
        record(
            f"我已经连续等待了约 {elapsed_seconds} 秒，期间没有新的外部输入需要立刻处理。最近一次等待理由：{reason}",
            source="action_runtime",
            action="wait_status",
            metadata={
                "elapsed_wait_seconds": elapsed_seconds,
                "threshold_seconds": threshold,
                "reason": reason,
                "low_frequency_wait_notice": True,
            },
        )

    def _action_write_memory(self, action: action_model.Action) -> str:
        note = str(action.args.get("memory_note") or action.args.get("note") or "").strip()
        tags = action.args.get("tags")
        if not isinstance(tags, list):
            tags = ["autonomous"]
        decision = AutonomousDecision(action="remember", memory_note=note, reason=action.reason, tags=tags)
        self._execute_remember(decision, events=[], context={}, trigger_reason="action_batch")
        return f"已整理记忆：{note}"

    def _action_update_cognition(self, action: action_model.Action) -> str:
        note = str(action.args.get("cognition_note") or action.args.get("note") or "").strip()
        decision = AutonomousDecision(action="update_cognition", cognition_note=note, reason=action.reason)
        self._execute_cognition(decision, events=[], context={}, trigger_reason="action_batch")
        return f"已触发认知更新：{note}"

    def _execute_search(self, decision: AutonomousDecision) -> None:
        batch = action_model.ActionBatch(
            actions=[
                action_model.Action(
                    action="search_web",
                    reason=decision.reason or "autonomous step selected web search",
                    args={
                        "query": decision.query,
                        "expected_use": "autonomous_step",
                    },
                    mode="sync",
                    visibility="private",
                    result_policy="feed_back",
                )
            ],
            reason=decision.reason or "autonomous web search",
        )
        self._runtime.execute_batch(batch)

    def _execute_remember(
        self,
        decision: AutonomousDecision,
        *,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str,
    ) -> None:
        if decision.memory_note:
            memory_events = getattr(self.session, "memory_events", None)
            if memory_events is not None and hasattr(memory_events, "add_direct_event"):
                memory_events.add_direct_event(decision.memory_note, tags=decision.tags or ["autonomous"])
            record = getattr(self.session, "record_self_action", None)
            if callable(record):
                record(
                    f"我把刚才的经历整理成了一条记忆：{decision.memory_note}",
                    source="inner_memory",
                    action="remember",
                    metadata={"tags": decision.tags or ["autonomous"], "reason": decision.reason},
                )
            return
        if self._memory_reflector is not None:
            self._memory_reflector.run_sync(
                inner_stream=context.get("inner_stream") or "",
                events=events,
                context=context,
                trigger_reason=trigger_reason,
            )

    def _execute_cognition(
        self,
        decision: AutonomousDecision,
        *,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str,
    ) -> None:
        if self._cognition_reflector is not None:
            self._cognition_reflector.run_sync(
                inner_stream=context.get("inner_stream") or "",
                events=events,
                context=context,
                trigger_reason=trigger_reason,
                note=decision.cognition_note,
            )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = str(text or "").strip().lstrip("\ufeff")
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _format_packet(packet: Any) -> str:
    if packet is None:
        return ""
    try:
        return qq_tool.format_packet_for_decision(packet)
    except Exception:
        pass
    if hasattr(packet, "conversation_id"):
        return (
            f"conversation_id: {getattr(packet, 'conversation_id', '')}\n"
            f"位置: {getattr(packet, 'label', '')}\n"
            f"类型: {getattr(packet, 'message_type', '')}\n"
            f"消息数: {getattr(packet, 'unread_count', '')}\n"
            f"最近消息:\n" + "\n".join(getattr(packet, "lines", []) or [])
        )
    if hasattr(packet, "content"):
        return str(packet.content)
    return str(packet)


def _resolve_qq_conversation_id(chosen: str, qq_packets: list[Any]) -> str:
    packets = [packet for packet in qq_packets if str(getattr(packet, "conversation_id", "") or "")]
    if not packets:
        return ""
    if len(packets) == 1:
        return str(getattr(packets[0], "conversation_id", "") or "")
    chosen_text = str(chosen or "").strip()
    if not chosen_text:
        return ""
    for packet in packets:
        label = str(getattr(packet, "label", "") or "")
        conversation_id = str(getattr(packet, "conversation_id", "") or "")
        if chosen_text == label or chosen_text in label or label in chosen_text:
            return conversation_id
    return ""


def _safe_context(obj: Any) -> str:
    if obj is None or not hasattr(obj, "get_context"):
        return ""
    try:
        return obj.get_context() or ""
    except Exception:
        return ""


