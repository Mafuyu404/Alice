"""Autonomous life runtime skeleton."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
import urllib.error

from kokoro.action import tool_spec
from kokoro.action import tools as action_tools
from kokoro.action.life_runtime import create_life_action_runtime
from kokoro.action.plan import ActionPlan, execute_action_plan
from kokoro.core import config as cfg
from kokoro.core import input_events
from kokoro.core import lifecycle_debug
from kokoro.memory.models import MemoryEventDraft
from kokoro.life.context_compactor import ContextCompactor
from kokoro.life.context_fragments import render_fragment
from kokoro.life.event_pool import InformationPool
from kokoro.life.local_thinking import LocalThinking
from kokoro.life.stream_patch import InnerStreamPatch, apply_inner_stream_patch
from kokoro.life.time_awareness import TimeAwareness
from kokoro.prompt import PromptContext, PromptManager
from kokoro.prompt.contracts import (
    LIFE_JSON_REPAIR_SCENE,
    LIFE_PATCH_FALLBACK_SCENE,
    LIFE_TICK_SCENE,
    LIFE_TOOL_SELECT_SCENE,
)
from kokoro.prompt.tools import discover_tool_prompt_specs, render_tool_catalog


_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class LifeTickResult:
    processed_events: int
    thought: str = ""
    patch_applied: bool = False
    thinking_intensity: int | None = None
    action_plan: dict[str, Any] | None = None
    action_plan_status: str = ""
    action_plan_error: str = ""


class LifeRuntime:
    """Carries the life loop without taking judgment away from the LLM."""

    def __init__(
        self,
        *,
        session,
        section: dict[str, Any] | None = None,
        llm=None,
        action_runtime=None,
        root: Path | None = None,
    ) -> None:
        self.session = session
        self.section = dict(section if section is not None else cfg.life_runtime_config())
        self.enabled = bool(self.section.get("enabled", False))
        self.root = Path(root or _ROOT)
        self.pool = InformationPool(max_events=int(self.section.get("pool_max_events", 512) or 512))
        self.time = TimeAwareness()
        self.llm = llm or LocalThinking(self.section.get("local_thinking", {}))
        memory_system = getattr(self.session, "memory_system", None)
        consolidator = getattr(memory_system, "consolidator", None)
        if consolidator is not None and getattr(consolidator, "llm_call", None) is None:
            consolidator.llm_call = self.llm.chat
        set_lifecycle_llm = getattr(memory_system, "set_lifecycle_llm", None)
        if callable(set_lifecycle_llm):
            set_lifecycle_llm(self.llm.chat)
        set_lifecycle_runtime_mode = getattr(memory_system, "set_lifecycle_runtime_mode", None)
        if callable(set_lifecycle_runtime_mode):
            set_lifecycle_runtime_mode(
                auto_wake=False,
                inline=bool(self.section.get("memory_core_inline", True)),
            )
        experience = getattr(memory_system, "experience", None)
        if experience is not None:
            if "memory_experience_batch_size" in self.section:
                experience.batch_size = max(1, int(self.section.get("memory_experience_batch_size") or 1))
            if "memory_experience_max_chars" in self.section:
                experience.max_chars = max(1000, int(self.section.get("memory_experience_max_chars") or 1000))
            if "memory_experience_catch_up_max_age_seconds" in self.section:
                experience.catch_up_max_age_seconds = max(
                    0.0,
                    float(self.section.get("memory_experience_catch_up_max_age_seconds") or 0.0),
                )
            if "memory_experience_catch_up_tail_events" in self.section:
                experience.catch_up_tail_events = max(
                    1,
                    int(self.section.get("memory_experience_catch_up_tail_events") or 1),
                )
        self.prompt_manager = PromptManager()
        self.tool_registry = tool_spec.ActionToolRegistry()
        action_tools.register_all(self.tool_registry)
        self.tool_prompt_specs = discover_tool_prompt_specs(_ROOT / "kokoro" / "action" / "tools")
        self.action_runtime = action_runtime or self._create_action_runtime()
        self._availability_cache: dict[str, tuple[float, bool]] = {}
        self._available_actions = self._resolve_available_actions()
        self.compactor = ContextCompactor(
            character_id=getattr(session, "character_id", "default"),
            root=self.root,
            llm_call=self.llm.chat,
            max_chars=int(self.section.get("context_max_chars", 8000) or 8000),
            prompt_manager=self.prompt_manager,
        )
        self._last_processed_sequence = 0
        self._inner_stream_version = 0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop_interval = float(self.section.get("idle_tick_seconds", 2.0) or 2.0)
        self._last_prompt_trace_dir: str | None = None
        self._last_memory_core_at = 0.0
        lifecycle_debug.log(
            "life.runtime.init",
            character_id=getattr(session, "character_id", ""),
            enabled=self.enabled,
        )

    def _create_action_runtime(self):
        return create_life_action_runtime(
            session=self.session,
            registry=self.tool_registry,
            section=self.section,
            search_section=cfg.inner_stream_search_config(),
        )

    def start(self) -> None:
        if not self.enabled:
            lifecycle_debug.log("life.runtime.start.disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"life-runtime-{getattr(self.session, 'character_id', 'unknown')}",
        )
        self._thread.start()
        memory_system = getattr(self.session, "memory_system", None)
        start_lifecycle = getattr(memory_system, "start_lifecycle_worker", None)
        if callable(start_lifecycle) and not bool(getattr(memory_system, "inline_maintenance_enabled", True)):
            memory_section = dict(self.section.get("memory_lifecycle", {}) or {})
            start_lifecycle(
                interval_seconds=float(memory_section.get("interval_seconds", 300.0) or 300.0),
                max_batches_per_wake=int(memory_section.get("max_batches_per_wake", 1) or 1),
            )
        lifecycle_debug.log("life.runtime.start", interval=self._loop_interval)

    def stop(self, *, wait: bool = True, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if wait and thread and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))
        flush_pending = getattr(self.action_runtime, "flush_pending", None)
        if callable(flush_pending):
            flush_pending()
        shutdown = getattr(self.action_runtime, "shutdown", None)
        if callable(shutdown):
            shutdown(wait=False)
        memory_system = getattr(self.session, "memory_system", None)
        stop_lifecycle = getattr(memory_system, "stop_lifecycle_worker", None)
        if callable(stop_lifecycle):
            stop_lifecycle(wait=wait)
        lifecycle_debug.log("life.runtime.stop", wait=wait)

    def submit(self, event: input_events.InputEvent) -> None:
        self.time.mark_event(event_type=event.type)
        pooled = self.pool.add(event)
        self.compactor.append_event(event, self.pool.format_batch([pooled], max_chars=1200))
        memory_policy = "debug" if event.type == "action_result" else "experience"
        self._append_memory_event(
            source="life_runtime",
            event_type="runtime_input",
            content=self.pool.format_batch([pooled], max_chars=1200),
            memory_policy=memory_policy,
            metadata={
                "input_type": event.type,
                "origin": event.source,
                "sequence": pooled.sequence,
            },
        )
        self._wake.set()

    def attach_action_runtime(self, action_runtime) -> None:
        self.action_runtime = action_runtime
        self._available_actions = self._resolve_available_actions()
        lifecycle_debug.log("life.runtime.action_runtime_attached", has_runtime=action_runtime is not None)

    def tick_once(self, *, force: bool = False) -> LifeTickResult:
        batch = self.pool.batch_since(
            self._last_processed_sequence,
            max_items=int(self.section.get("batch_max_events", 32) or 32),
        )
        if not batch and not force:
            return LifeTickResult(processed_events=0)
        if batch:
            self._last_processed_sequence = max(item.sequence for item in batch)
        inner_stream = _inner_stream_text(self.session)
        time_context = self.time.render(pending_lines=self.pool.timing_lines(batch))
        event_text = self.pool.format_batch(batch, max_chars=int(self.section.get("batch_max_chars", 4000) or 4000))
        qq_private_observations = _qq_private_observations(batch)
        for observation in qq_private_observations:
            lifecycle_debug.log("life.runtime.qq_private_observed", **observation)
        if qq_private_observations:
            self._update_recent_qq_context(qq_private_observations[-1], event_text=event_text)
        digest = self.compactor.compact_once(time_context=time_context, inner_stream=inner_stream)
        if qq_private_observations and _digest_may_have_lost_qq_private(digest, qq_private_observations):
            lifecycle_debug.log(
                "life.context.compact.qq_private_possible_drop",
                observations=qq_private_observations,
                digest=digest,
            )
        if digest:
            self._append_memory_event(
                source="life_runtime",
                event_type="context_digest",
                content=digest,
                memory_policy="debug",
                metadata={"phase": "before_think"},
            )
        thought = self._think(
            inner_stream=inner_stream,
            time_context=time_context,
            digest=digest,
            event_text=event_text,
        )
        thought, data = self._record_and_parse_thought(thought, processed_events=len(batch))
        self.time.mark_llm_thought()
        patch_applied = self._apply_patch_from_data(data, inner_stream=inner_stream)
        self._record_pending_threads_from_data(data)
        if qq_private_observations and not _extract_action_intent_from_data(data):
            lifecycle_debug.log(
                "life.runtime.qq_private.no_action_intent",
                observations=qq_private_observations,
                thought_snippet=str(thought or "")[:800],
            )
        action_intent = _extract_action_intent_from_data(data)
        action_plan = self._select_action_plan_from_data(
            data,
            inner_stream=inner_stream,
            time_context=time_context,
            digest=digest,
            event_text=event_text,
        )
        if action_plan and action_intent:
            action_plan = _attach_expression_intent(action_plan, action_intent)
        if qq_private_observations and _extract_action_intent_from_data(data) and not action_plan:
            lifecycle_debug.log(
                "life.runtime.qq_private.tool_select_no_plan",
                observations=qq_private_observations,
                action_intent=_extract_action_intent_from_data(data),
            )
        self._write_prompt_trace_result(llm_raw=thought, parsed=data, tool_plan=action_plan)
        if action_plan:
            self._append_memory_event(
                source="life_runtime",
                event_type="action_plan_selected",
                content=json.dumps(action_plan, ensure_ascii=False, indent=2, default=str),
                memory_policy="debug",
                metadata={"trace_dir": self._last_prompt_trace_dir or ""},
            )
        intensity = _extract_intensity_from_data(data)
        action_plan_status = ""
        action_plan_error = ""
        action_results: dict[str, str] = {}
        if action_plan:
            action_plan_status, action_plan_error, action_results = self._execute_action_plan(action_plan)
            self._append_memory_event(
                source="life_runtime",
                event_type="action_plan_finished",
                content=json.dumps(
                    {
                        "status": action_plan_status,
                        "error": action_plan_error,
                        "results": action_results,
                        "plan": action_plan,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                memory_policy="debug",
                metadata={"trace_dir": self._last_prompt_trace_dir or ""},
            )
        if action_plan_status == "executed" and action_results and _plan_opens_followup(action_plan or {}):
            followup = self._continue_after_action_results(
                initial_plan=action_plan or {},
                initial_results=action_results,
            )
            if followup.get("patch_applied"):
                patch_applied = True
            if followup.get("thinking_intensity") is not None:
                intensity = followup["thinking_intensity"]
            if followup.get("action_plan"):
                action_plan = followup["action_plan"]
                action_plan_status = str(followup.get("action_plan_status") or action_plan_status)
                action_plan_error = str(followup.get("action_plan_error") or action_plan_error)
        elif (
            action_plan_status == "executed"
            and action_results
            and _plan_has_self_expression(action_plan or {})
            and not _action_results_indicate_blocked_expression(action_results)
        ):
            self._close_self_expression_loop(
                reason="self_expression_completed",
                action_intent=action_intent,
                plan=action_plan or {},
                results=action_results,
            )
        hot_path = bool(batch) or bool(action_plan_status)
        self._run_memory_core_cycle(defer_if_hot=hot_path)
        lifecycle_debug.log(
            "life.runtime.tick_done",
            processed_events=len(batch),
            patch_applied=patch_applied,
            thinking_intensity=intensity,
            has_action_plan=bool(action_plan),
            action_plan_status=action_plan_status,
            action_plan_error=action_plan_error,
        )
        if intensity is not None:
            self._loop_interval = self._interval_from_intensity(intensity)
        return LifeTickResult(
            processed_events=len(batch),
            thought=thought,
            patch_applied=patch_applied,
            thinking_intensity=intensity,
            action_plan=action_plan,
            action_plan_status=action_plan_status,
            action_plan_error=action_plan_error,
        )

    def _record_and_parse_thought(self, thought: str, *, processed_events: int) -> tuple[str, dict[str, Any] | None]:
        data = _extract_json(thought)
        self._write_prompt_trace_result(llm_raw=thought, parsed=data, tool_plan=None)
        lifecycle_debug.log(
            "life.runtime.thought_raw",
            chars=len(thought or ""),
            text=thought,
            json_ok=isinstance(data, dict),
        )
        self._append_memory_event(
            source="life_runtime",
            event_type="inner_thought",
            content=thought,
            memory_policy="debug",
            metadata={
                "json_ok": isinstance(data, dict),
                "processed_events": processed_events,
                "trace_dir": self._last_prompt_trace_dir or "",
            },
        )
        if isinstance(data, dict):
            return thought, data

        parse_reason = _json_parse_error(thought)
        lifecycle_debug.log(
            "life.runtime.thought_parse_failed",
            reason=parse_reason,
            text=thought,
        )
        self._append_memory_event(
            source="life_runtime",
            event_type="inner_thought_parse_failed",
            content=f"{parse_reason}\n\n{thought}",
            memory_policy="debug",
            metadata={"trace_dir": self._last_prompt_trace_dir or ""},
        )
        repaired = self._repair_json_thought(thought, parse_reason=parse_reason)
        if not repaired:
            return thought, data

        repaired_data = _extract_json(repaired)
        lifecycle_debug.log(
            "life.runtime.thought_repair_raw",
            chars=len(repaired),
            text=repaired,
            json_ok=isinstance(repaired_data, dict),
        )
        if isinstance(repaired_data, dict):
            lifecycle_debug.log("life.runtime.thought_repair_applied")
            self._append_memory_event(
                source="life_runtime",
                event_type="inner_thought_repaired",
                content=repaired,
                memory_policy="debug",
                metadata={"trace_dir": self._last_prompt_trace_dir or ""},
            )
            return repaired, repaired_data

        lifecycle_debug.log(
            "life.runtime.thought_repair_failed",
            reason=_json_parse_error(repaired),
        )
        self._append_memory_event(
            source="life_runtime",
            event_type="inner_thought_repair_failed",
            content=f"{_json_parse_error(repaired)}\n\n{repaired}",
            memory_policy="debug",
            metadata={"trace_dir": self._last_prompt_trace_dir or ""},
        )
        return thought, data

    def _continue_after_action_results(
        self,
        *,
        initial_plan: dict[str, Any],
        initial_results: dict[str, str],
    ) -> dict[str, Any]:
        max_rounds = max(0, int(self.section.get("tool_followup_rounds", 1) or 0))
        if max_rounds <= 0:
            return {}

        state: dict[str, Any] = {}
        current_plan = initial_plan
        current_results = initial_results
        seen_plans = {_canonical_plan_key(initial_plan)}
        for round_index in range(max_rounds):
            result_context = self._absorb_action_results_context(
                plan=current_plan,
                results=current_results,
                round_index=round_index,
            )
            inner_stream = _inner_stream_text(self.session)
            time_context = self.time.render(
                pending_lines=[
                    "Tool results have just returned inside the same life tick; understand them before deciding whether anything else changes."
                ]
            )
            thought = self._think(
                inner_stream=inner_stream,
                time_context=time_context,
                digest=self.compactor.recent_digest(),
                event_text=result_context,
            )
            thought, data = self._record_and_parse_thought(thought, processed_events=0)
            self.time.mark_llm_thought()
            patch_applied = self._apply_patch_from_data(data, inner_stream=inner_stream)
            self._record_pending_threads_from_data(data)
            action_plan = self._select_action_plan_from_data(
                data,
                inner_stream=inner_stream,
                time_context=time_context,
                digest=self.compactor.recent_digest(),
                event_text=result_context,
            )
            self._write_prompt_trace_result(llm_raw=thought, parsed=data, tool_plan=action_plan)
            intensity = _extract_intensity_from_data(data)
            state.update(
                {
                    "thought": thought,
                    "patch_applied": bool(state.get("patch_applied")) or patch_applied,
                    "thinking_intensity": intensity if intensity is not None else state.get("thinking_intensity"),
                    "action_plan": action_plan or state.get("action_plan"),
                }
            )
            lifecycle_debug.log(
                "life.runtime.tool_followup.done",
                round=round_index + 1,
                patch_applied=patch_applied,
                thinking_intensity=intensity,
                has_action_plan=bool(action_plan),
            )
            if not action_plan:
                break
            plan_key = _canonical_plan_key(action_plan)
            if plan_key in seen_plans:
                lifecycle_debug.log("life.runtime.tool_followup.repeat_plan_stop", action_plan=action_plan)
                state.update(
                    {
                        "action_plan_status": "skipped_repeat",
                        "action_plan_error": "follow-up repeated an already executed action plan",
                    }
                )
                break
            seen_plans.add(plan_key)
            status, error, results = self._execute_action_plan(action_plan)
            self._append_memory_event(
                source="life_runtime",
                event_type="action_plan_finished",
                content=json.dumps(
                    {
                        "status": status,
                        "error": error,
                        "results": results,
                        "plan": action_plan,
                        "phase": "tool_followup",
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                memory_policy="debug",
                metadata={"trace_dir": self._last_prompt_trace_dir or ""},
            )
            state.update({"action_plan_status": status, "action_plan_error": error})
            if status != "executed" or not results:
                break
            current_plan = action_plan
            current_results = results
        return state

    def _absorb_action_results_context(
        self,
        *,
        plan: dict[str, Any],
        results: dict[str, str],
        round_index: int,
    ) -> str:
        text = _format_action_results(plan, results)
        if not text:
            return ""
        self.time.mark_event(event_type="action_result")
        self.compactor.append_live(text)
        self.compactor.append_tool_result(text)
        self._append_memory_event(
            source="life_runtime",
            event_type="tool_results_same_tick",
            content=text,
            memory_policy="debug",
            metadata={"round": round_index + 1},
        )
        lifecycle_debug.log(
            "life.runtime.tool_results.same_tick_context",
            round=round_index + 1,
            chars=len(text),
            text=text,
        )
        return text

    def _execute_action_plan(self, raw_plan: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
        if self.action_runtime is None:
            lifecycle_debug.log("life.runtime.action_plan.no_runtime", action_plan=raw_plan)
            return "rejected", "action runtime is not available", {}
        try:
            plan = ActionPlan.from_dict(raw_plan)
            validation_error = self._validate_action_plan(plan)
            if validation_error:
                lifecycle_debug.log(
                    "life.runtime.action_plan.rejected",
                    plan=plan,
                    error=validation_error,
                )
                return "rejected", validation_error, {}
            lifecycle_debug.log("life.runtime.action_plan.execute", plan=plan)
            results = execute_action_plan(plan, self.action_runtime)
            lifecycle_debug.log("life.runtime.action_plan.done", plan=plan, results=results)
            return "executed", "", results
        except Exception as exc:
            lifecycle_debug.log("life.runtime.action_plan.error", action_plan=raw_plan, error=str(exc))
            return "error", str(exc), {}

    def _run(self) -> None:
        while not self._stop.is_set():
            woke = self._wake.wait(timeout=max(0.05, self._loop_interval))
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.tick_once(force=not woke)
            except Exception as exc:
                lifecycle_debug.log("life.runtime.loop_error", error=str(exc))

    def _interval_from_intensity(self, intensity: int) -> float:
        min_seconds = float(self.section.get("min_tick_seconds", 0.25) or 0.25)
        max_seconds = float(self.section.get("max_tick_seconds", 8.0) or 8.0)
        value = max(0, min(100, int(intensity)))
        ratio = 1.0 - (value / 100.0)
        return max(min_seconds, min(max_seconds, min_seconds + (max_seconds - min_seconds) * ratio))

    def _think(self, *, inner_stream: str, time_context: str, digest: str, event_text: str) -> str:
        character_name = str(getattr(self.session, "character_name", "") or getattr(self.session, "character_id", "AI"))
        character_profile = _character_profile_text(self.session)
        active_event_text = event_text or self._recent_qq_context_text()
        cognition_context = _cognition_context_text(self.session, event_text=active_event_text)
        memory_context = self._memory_context(event_text=active_event_text, inner_stream=inner_stream, digest=digest)
        fragment_max = int(self.section.get("context_fragment_max_chars", 4000) or 4000)
        trace_dir = None
        debug_dir = self.section.get("prompt_trace_dir")
        if debug_dir:
            trace_dir = str(Path(debug_dir) / f"{int(time.time() * 1000)}_life_tick")
        self._last_prompt_trace_dir = trace_dir
        ctx = PromptContext(
            scene=LIFE_TICK_SCENE,
            character_id=str(getattr(self.session, "character_id", "") or ""),
            character_name=character_name,
            debug_mode=bool(self.section.get("debug", False)),
            trace_dir=trace_dir,
            values={
                "character_profile": self._context_fragment("character_profile", character_profile, max_chars=fragment_max),
                "cognition_context": self._context_fragment("cognition", cognition_context, max_chars=fragment_max),
                "memory_context": self._context_fragment("memory", memory_context, max_chars=fragment_max),
                "inner_stream": self._context_fragment("inner_stream", inner_stream or "(empty)", max_chars=fragment_max),
                "inner_stream_version": self._inner_stream_version,
                "time_context": self._context_fragment("time_awareness", time_context, max_chars=2000),
                "context_digest": self._context_fragment("recent_digest", digest, max_chars=fragment_max),
                "event_batch": self._context_fragment(
                    "event_batch",
                    active_event_text or "(no new external event; this can be time passing or continued thinking)",
                    max_chars=int(self.section.get("event_batch_fragment_max_chars", min(fragment_max, 2400)) or 2400),
                ),
                "pending_threads": self._context_fragment(
                    "pending_threads",
                    self.compactor.pending_threads(),
                    max_chars=fragment_max,
                ),
            },
        )
        messages = self.prompt_manager.render(LIFE_TICK_SCENE, ctx)
        lifecycle_debug.log(
            "life.runtime.prompt_rendered",
            scene=LIFE_TICK_SCENE,
            messages=len(messages),
            trace_dir=trace_dir or "",
        )
        return self.llm.chat(
            messages,
            {
                "function": "life_tick",
                "max_tokens": int(self.section.get("tick_max_tokens", 640) or 640),
                "timeout": int(self.section.get("tick_timeout", 90) or 90),
            },
        )

    def _select_action_plan_from_data(
        self,
        data: dict[str, Any] | None,
        *,
        inner_stream: str,
        time_context: str,
        digest: str,
        event_text: str = "",
    ) -> dict[str, Any] | None:
        if not isinstance(data, dict):
            return None
        direct_plan = _extract_action_plan_from_data(data)
        if direct_plan:
            lifecycle_debug.log("life.runtime.direct_action_plan_ignored", action_plan=direct_plan)
        intent = _extract_action_intent_from_data(data)
        if not intent:
            return None
        return self._select_action_plan(
            action_intent=intent,
            inner_stream=inner_stream,
            time_context=time_context,
            digest=digest,
            event_text=event_text,
        )

    def _select_action_plan(
        self,
        *,
        action_intent: str,
        inner_stream: str,
        time_context: str,
        digest: str,
        event_text: str = "",
    ) -> dict[str, Any] | None:
        self._available_actions = self._resolve_available_actions()
        if not self._available_actions:
            lifecycle_debug.log("life.runtime.tool_select.no_available_actions", action_intent=action_intent)
            return None
        fragment_max = int(self.section.get("context_fragment_max_chars", 4000) or 4000)
        active_event_text = event_text or self._recent_qq_context_text()
        trace_dir = str(Path(self._last_prompt_trace_dir) / "tool_select") if self._last_prompt_trace_dir else None
        ctx = PromptContext(
            scene=LIFE_TOOL_SELECT_SCENE,
            character_id=str(getattr(self.session, "character_id", "") or ""),
            character_name=str(getattr(self.session, "character_name", "") or getattr(self.session, "character_id", "AI")),
            debug_mode=bool(self.section.get("debug", False)),
            trace_dir=trace_dir,
            values={
                "action_intent": self._context_fragment(
                    "action_intent",
                    action_intent,
                    max_chars=1200,
                    kind="intent",
                    audience="tool_select",
                    role="subject",
                ),
                "inner_stream": self._context_fragment(
                    "inner_stream",
                    inner_stream or "(empty)",
                    max_chars=fragment_max,
                    kind="subject_state",
                    audience="tool_select",
                    role="subject",
                ),
                "time_context": self._context_fragment(
                    "time_awareness",
                    time_context,
                    max_chars=1600,
                    kind="time",
                    audience="tool_select",
                    role="environment",
                ),
                "event_batch": self._context_fragment(
                    "event_batch",
                    active_event_text or "(none)",
                    max_chars=int(self.section.get("tool_select_event_max_chars", 2400) or 2400),
                    kind="event_batch",
                    audience="tool_select",
                    role="environment",
                ),
                "context_digest": self._context_fragment(
                    "recent_digest",
                    digest,
                    max_chars=fragment_max,
                    kind="digest",
                    audience="tool_select",
                    role="environment",
                ),
                "recent_self_expressions": self._context_fragment(
                    "recent_self_expressions",
                    self._recent_self_expression_context(),
                    max_chars=1600,
                    kind="recent_actions",
                    audience="tool_select",
                    role="environment",
                ),
                "tool_capabilities": self._context_fragment(
                    "tool_capabilities",
                    self._tool_capabilities_text(),
                    max_chars=max(fragment_max, 6000),
                    kind="tool_catalog",
                    audience="tool_select",
                    role="system",
                ),
            },
        )
        messages = self.prompt_manager.render(LIFE_TOOL_SELECT_SCENE, ctx)
        lifecycle_debug.log(
            "life.runtime.tool_select.prompt_rendered",
            scene=LIFE_TOOL_SELECT_SCENE,
            messages=len(messages),
            trace_dir=trace_dir or "",
        )
        try:
            raw = self.llm.chat(
                messages,
                {
                    "function": "life_tool_select",
                    "max_tokens": int(self.section.get("tool_select_max_tokens", 700) or 700),
                    "timeout": int(self.section.get("tool_select_timeout", 60) or 60),
                },
            )
        except Exception as exc:
            lifecycle_debug.log("life.runtime.tool_select.error", error=str(exc))
            return None
        parsed = _extract_json(raw)
        plan = _extract_action_plan_from_data(parsed)
        lifecycle_debug.log(
            "life.runtime.tool_select.raw",
            text=raw,
            json_ok=isinstance(parsed, dict),
            has_action_plan=bool(plan),
        )
        return _preserve_search_query_anchors(
            plan,
            "\n".join(
                part
                for part in (action_intent, event_text, inner_stream, digest, self.compactor.pending_threads())
                if str(part or "").strip()
            ),
        )

    def _context_fragment(
        self,
        source: str,
        content: str,
        *,
        max_chars: int,
        kind: str = "material",
        audience: str = "life_tick",
        role: str = "environment",
    ) -> str:
        return render_fragment(
            source,
            content or "(none)",
            max_chars=max_chars,
            kind=kind,
            audience=audience,
            role=role,
        )

    def _update_recent_qq_context(self, observation: dict[str, Any], *, event_text: str) -> None:
        if self.action_runtime is None or not hasattr(self.action_runtime, "tool_context"):
            return
        conversation_id = str(observation.get("conversation_id") or "").strip()
        if not conversation_id:
            return
        self.action_runtime.tool_context["recent_qq_conversation_id"] = conversation_id
        self.action_runtime.tool_context["recent_qq_event_batch"] = event_text
        self.action_runtime.tool_context["recent_qq_context_updated_at"] = time.monotonic()
        lifecycle_debug.log(
            "life.runtime.qq_context_for_tools",
            conversation_id=conversation_id,
            event_chars=len(event_text or ""),
        )

    def _recent_qq_context_text(self) -> str:
        if self.action_runtime is None or not hasattr(self.action_runtime, "tool_context"):
            return ""
        ctx = self.action_runtime.tool_context
        event_text = str(ctx.get("recent_qq_event_batch") or "").strip()
        if not event_text:
            return ""
        updated_at = ctx.get("recent_qq_context_updated_at")
        try:
            age = time.monotonic() - float(updated_at)
        except Exception:
            age = 0.0
        max_age = float(self.section.get("recent_qq_context_seconds", 240) or 240)
        if age > max_age:
            return ""
        return f"仍有余温的QQ对话现场（约 {int(max(0, age))} 秒前）：\n{event_text}"

    def _close_recent_qq_context(self, *, reason: str, plan: dict[str, Any]) -> None:
        if self.action_runtime is None or not hasattr(self.action_runtime, "tool_context"):
            return
        ctx = self.action_runtime.tool_context
        if not ctx.get("recent_qq_event_batch"):
            return
        ctx["recent_qq_event_batch"] = ""
        ctx["recent_qq_context_closed_at"] = time.monotonic()
        lifecycle_debug.log(
            "life.runtime.qq_context_closed",
            reason=reason,
            plan=plan,
        )

    def _close_self_expression_loop(
        self,
        *,
        reason: str,
        action_intent: str = "",
        plan: dict[str, Any],
        results: dict[str, str],
    ) -> None:
        self._record_recent_self_expression(plan, action_intent=action_intent)
        self._close_recent_qq_context(reason=reason, plan=plan)
        self.compactor.clear_pending_threads()
        self.compactor.clear_tool_results()
        closure = _self_expression_closure_text(plan, results, action_intent=action_intent)
        if closure:
            stream = getattr(self.session, "inner_stream", None)
            current = _inner_stream_text(self.session)
            patch = InnerStreamPatch(
                patches=[{"op": "append", "text": closure}],
                reason="self expression closed",
            )
            max_chars = int(cfg.inner_stream_config().get("max_chars", 1200) or 1200)
            result = apply_inner_stream_patch(current, patch, max_chars=max_chars)
            if result.applied and stream is not None:
                apply_patch = getattr(stream, "apply_patch", None)
                raw_patch = {
                    "patches": [{"op": "append", "text": closure}],
                    "reason": "self expression closed",
                }
                if callable(apply_patch):
                    apply_patch(raw_patch)
                elif hasattr(stream, "text"):
                    stream.text = result.text
                self._inner_stream_version += 1
                lifecycle_debug.log(
                    "life.runtime.self_expression_closed_inner_stream",
                    chars=len(closure),
                    text=closure,
                )

    def _append_memory_event(
        self,
        *,
        source: str,
        event_type: str,
        content: str,
        memory_policy: str = "experience",
        metadata: dict[str, Any] | None = None,
        tool_name: str = "",
    ) -> None:
        text = str(content or "").strip()
        if not text:
            return
        memory_system = getattr(self.session, "memory_system", None)
        append_event = getattr(memory_system, "append_event", None)
        if not callable(append_event):
            return
        try:
            append_event(
                MemoryEventDraft(
                    character_id=str(getattr(self.session, "character_id", "default") or "default"),
                    source=source,
                    event_type=event_type,
                    content=text,
                    memory_policy=memory_policy,  # type: ignore[arg-type]
                    tool_name=tool_name,
                    metadata=dict(metadata or {}),
                )
            )
        except Exception as exc:
            lifecycle_debug.log("life.runtime.memory_event_append_error", event_type=event_type, error=str(exc))

    def _run_memory_core_cycle(self, *, defer_if_hot: bool = False) -> None:
        memory_system = getattr(self.session, "memory_system", None)
        if memory_system is not None and not bool(getattr(memory_system, "inline_maintenance_enabled", True)):
            wake = getattr(memory_system, "wake_lifecycle_worker", None)
            now = time.monotonic()
            last = float(getattr(self, "_last_memory_worker_wake", 0.0) or 0.0)
            wake_seconds = float(self.section.get("memory_core_wake_seconds", 300.0) or 300.0)
            if callable(wake) and now - last >= max(1.0, wake_seconds):
                self._last_memory_worker_wake = now
                wake()
                lifecycle_debug.log("life.runtime.memory_core_deferred", wake=True)
            else:
                lifecycle_debug.log("life.runtime.memory_core_deferred", wake=False)
            return
        maintenance = getattr(memory_system, "maintenance_once", None)
        if not callable(maintenance):
            return
        if defer_if_hot:
            lifecycle_debug.log("life.runtime.memory_core_deferred", hot_path=True)
            return
        now = time.monotonic()
        interval = float(self.section.get("memory_core_interval_seconds", 20.0) or 20.0)
        if now - self._last_memory_core_at < max(1.0, interval):
            lifecycle_debug.log("life.runtime.memory_core_deferred", inline=True, interval_seconds=interval)
            return
        self._last_memory_core_at = now
        try:
            decisions = maintenance(max_batches=int(self.section.get("memory_core_batches_per_tick", 1) or 1))
        except Exception as exc:
            lifecycle_debug.log("life.runtime.memory_core_error", error=str(exc))
            return
        remembered = sum(len(getattr(decision, "remember", []) or []) for decision in decisions or [])
        archived = sum(len(getattr(decision, "archive", []) or []) for decision in decisions or [])
        notes = [str(getattr(decision, "notes", "") or "").strip() for decision in decisions or []]
        notes = [note for note in notes if note]
        lifecycle_debug.log(
            "life.runtime.memory_core_cycle",
            decisions=len(decisions or []),
            remembered=remembered,
            archived=archived,
            notes=notes,
            experience=getattr(memory_system, "last_experience_result", None),
        )
        if remembered or archived or notes:
            self._append_memory_event(
                source="life_runtime",
                event_type="memory_core_cycle",
                content=json.dumps(
                    {
                        "decisions": len(decisions or []),
                        "remembered": remembered,
                        "archived": archived,
                        "notes": notes,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                memory_policy="debug",
                metadata={"remembered": remembered, "archived": archived},
            )

    def _memory_context(self, *, event_text: str, inner_stream: str, digest: str) -> str:
        memory_system = getattr(self.session, "memory_system", None)
        if memory_system is not None:
            try:
                context = str(
                    memory_system.default_context(
                        event_text=event_text,
                        inner_stream=inner_stream,
                        recent_digest=digest,
                        pending_threads=self.compactor.pending_threads(),
                    )
                    or ""
                ).strip()
                if context:
                    self._append_memory_event(
                        source="life_runtime",
                        event_type="memory_context_presented",
                        content=context,
                        memory_policy="debug",
                        metadata={
                            "event_chars": len(event_text or ""),
                            "inner_stream_chars": len(inner_stream or ""),
                            "digest_chars": len(digest or ""),
                        },
                    )
                return context
            except Exception as exc:
                lifecycle_debug.log("life.runtime.memory_system_context_error", error=str(exc))

        backend = getattr(self.session, "memory_backend", None)
        if backend is None or not getattr(backend, "ready", False):
            return ""
        query = "\n".join(
            part
            for part in (
                str(event_text or "").strip(),
                str(inner_stream or "").strip(),
                str(digest or "").strip(),
            )
            if part
        )[:1200]
        if not query:
            character_data = getattr(self.session, "character_data", {}) or {}
            query = " ".join(
                str(character_data.get(key) or "")
                for key in ("description", "personality", "background", "relationship")
            )[:1200]
        if not query:
            return ""
        try:
            from kokoro.core import memory as memory_mod

            user_ids = memory_mod.context_user_ids(
                str(getattr(self.session, "character_id", "default") or "default"),
            )
            if hasattr(backend, "get_context_multi"):
                return str(backend.get_context_multi(query, user_ids) or "").strip()
            return str(backend.get_context(query, user_id=user_ids[0]) or "").strip()
        except Exception as exc:
            lifecycle_debug.log("life.runtime.memory_context_error", error=str(exc))
            return ""

    def _repair_json_thought(self, raw: str, *, parse_reason: str) -> str:
        if not str(raw or "").strip():
            return ""
        messages = self.prompt_manager.render(
            LIFE_JSON_REPAIR_SCENE,
            PromptContext(
                scene=LIFE_JSON_REPAIR_SCENE,
                character_id=str(getattr(self.session, "character_id", "") or ""),
                character_name=str(getattr(self.session, "character_name", "") or ""),
                values={
                    "parse_reason": parse_reason or "(unknown)",
                    "raw_output": raw,
                },
            ),
        )
        try:
            return self.llm.chat(
                messages,
                {
                    "function": "life_tick_json_repair",
                    "max_tokens": int(self.section.get("json_repair_max_tokens", 900) or 900),
                },
            ).strip()
        except Exception as exc:
            lifecycle_debug.log("life.runtime.thought_repair_error", error=str(exc))
            return ""

    def _write_prompt_trace_result(
        self,
        *,
        llm_raw: str,
        parsed: dict[str, Any] | None,
        tool_plan: dict[str, Any] | None,
    ) -> None:
        if not self._last_prompt_trace_dir:
            return
        try:
            trace_path = Path(self._last_prompt_trace_dir)
            trace_path.mkdir(parents=True, exist_ok=True)
            (trace_path / "llm_raw.txt").write_text(str(llm_raw or ""), encoding="utf-8")
            (trace_path / "parsed.json").write_text(
                json.dumps(parsed or {}, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            (trace_path / "tool_plan.json").write_text(
                json.dumps(tool_plan or {}, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            lifecycle_debug.log("life.runtime.prompt_trace_write_error", error=str(exc))

    def _tool_capabilities_text(self) -> str:
        actions = sorted(self._available_actions)
        schemas = self.tool_registry.enabled_schemas()
        lines = ["Registered action names: " + ", ".join(actions)]
        prompt_catalog = render_tool_catalog(
            self.tool_prompt_specs,
            actions,
            include_stage_prompts=bool(self.section.get("include_tool_stage_prompts_in_life_prompt", False)),
            stage_prompt_max_chars=int(self.section.get("tool_stage_prompt_max_chars", 180) or 180),
        )
        if prompt_catalog:
            lines.append("Tool prompt catalog:")
            lines.append(prompt_catalog)
        if schemas:
            lines.append("Tool schemas:")
            for schema in schemas:
                fn = schema.get("function", {}) if isinstance(schema, dict) else {}
                name = str(fn.get("name") or "").strip()
                if name not in self._available_actions:
                    continue
                description = str(fn.get("description") or "").strip()
                required = fn.get("parameters", {}).get("required", []) if isinstance(fn.get("parameters"), dict) else []
                required_text = f" Required args: {', '.join(required)}." if required else ""
                if name:
                    lines.append(f"- {name}: {description}{required_text}")
        return "\n".join(lines)

    def _resolve_available_actions(self) -> set[str]:
        actions = set(self.tool_registry.registered_actions())
        context = getattr(self.action_runtime, "tool_context", {}) if self.action_runtime is not None else {}
        unavailable: set[str] = set()
        if context.get("say_resources") is None:
            unavailable.update({"say", "say_precomputed"})
        if not callable(context.get("qq_send_message")):
            unavailable.add("send_qq_message")
        if context.get("vts_controller") is None:
            unavailable.add("vts_expression")
        if context.get("vts_body_driver") is None and context.get("vts_arbiter") is None:
            unavailable.add("vts_motion")
        unavailable.update({"search_memory", "save_to_memory", "write_conversation_memory"})
        observe_section = cfg.get("observe_screen", {})
        if isinstance(observe_section, dict) and observe_section.get("enabled") is False:
            unavailable.update({"observe_screen", "look_at_screen"})
        if not self._cached_availability("search_web", lambda: _web_search_available(context.get("web_search_client"))):
            unavailable.add("search_web")
        if context.get("task_manager") is None:
            unavailable.update({"claude_code_exec", "check_task_progress", "list_active_tasks", "cancel_task"})
        return actions - unavailable

    def _cached_availability(self, key: str, probe) -> bool:
        now = time.monotonic()
        ttl = float(self.section.get("availability_check_seconds", 30.0) or 30.0)
        cached = self._availability_cache.get(key)
        if cached and now - cached[0] < ttl:
            return cached[1]
        available = bool(probe())
        self._availability_cache[key] = (now, available)
        lifecycle_debug.log("life.runtime.tool_availability", tool=key, available=available)
        return available

    def _missing_required_args(self, action_name: str, args: dict[str, Any]) -> list[str]:
        spec = self.tool_registry.resolve(action_name)
        if spec is not None and spec.prepare is not None:
            return []
        missing: list[str] = []
        for schema in self.tool_registry.enabled_schemas():
            fn = schema.get("function", {}) if isinstance(schema, dict) else {}
            if fn.get("name") != action_name:
                continue
            params = fn.get("parameters", {})
            required = params.get("required", []) if isinstance(params, dict) else []
            for key in required:
                if not str(args.get(str(key)) or "").strip():
                    missing.append(str(key))
            return missing
        return []

    def _validate_action_plan(self, plan: ActionPlan) -> str:
        self._available_actions = self._resolve_available_actions()
        for node in plan.nodes:
            if node.tool not in self._available_actions:
                return f"action is not available in this runtime: {node.tool}"
            missing = self._missing_required_args(node.tool, node.args)
            if node.tool == "write_conversation_memory" and not str(node.args.get("trigger_text") or "").strip():
                missing.append("trigger_text")
            if node.tool == "send_qq_message" and not str(
                node.args.get("message") or node.args.get("content") or node.args.get("text") or ""
            ).strip():
                missing.append("message")
            if missing:
                return f"action {node.tool} missing required args: {', '.join(dict.fromkeys(missing))}"
            if node.tool == "send_qq_message":
                duplicate = self._recent_self_expression_duplicate(node.args)
                if duplicate:
                    lifecycle_debug.log(
                        "life.runtime.self_expression_duplicate_rejected",
                        tool=node.tool,
                        reason=duplicate,
                        args=node.args,
                    )
                    return duplicate
        return ""

    def _record_recent_self_expression(self, plan: dict[str, Any], *, action_intent: str = "") -> None:
        if self.action_runtime is None or not hasattr(self.action_runtime, "tool_context"):
            return
        message = _first_self_expression_message(plan)
        if not message:
            return
        conversation_id = _first_self_expression_conversation_id(plan)
        if not conversation_id:
            conversation_id = str(
                self.action_runtime.tool_context.get("recent_qq_conversation_id") or ""
            ).strip()
        record = {
            "at": time.monotonic(),
            "conversation_id": conversation_id,
            "message": message,
            "intent": str(action_intent or _plan_expression_intent(plan) or "").strip(),
            "intent_key": _expression_intent_key(action_intent or _plan_expression_intent(plan) or ""),
        }
        recent = self.action_runtime.tool_context.setdefault("recent_self_expressions", [])
        if not isinstance(recent, list):
            recent = []
            self.action_runtime.tool_context["recent_self_expressions"] = recent
        recent.append(record)
        max_records = int(self.section.get("recent_self_expression_records", 24) or 24)
        del recent[:-max_records]
        lifecycle_debug.log(
            "life.runtime.self_expression_recorded",
            conversation_id=conversation_id,
            message=message,
            intent=record["intent"],
            intent_key=record["intent_key"],
        )

    def _recent_self_expression_context(self) -> str:
        if self.action_runtime is None or not hasattr(self.action_runtime, "tool_context"):
            return "(none)"
        recent = self.action_runtime.tool_context.get("recent_self_expressions", [])
        if not isinstance(recent, list) or not recent:
            return "(none)"
        now = time.monotonic()
        lines: list[str] = []
        for item in reversed(recent[-8:]):
            if not isinstance(item, dict):
                continue
            try:
                age = int(max(0.0, now - float(item.get("at") or 0.0)))
            except Exception:
                age = 0
            message = str(item.get("message") or "").strip()
            intent = str(item.get("intent") or "").strip()
            if not message:
                continue
            line = f"- {age}秒前已经发送：{message}"
            if intent:
                line += f"；当时的表达意图：{intent}"
            lines.append(line)
        return "\n".join(lines) if lines else "(none)"

    def _recent_self_expression_duplicate(self, args: dict[str, Any]) -> str:
        if self.action_runtime is None or not hasattr(self.action_runtime, "tool_context"):
            return ""
        message = str(args.get("message") or args.get("content") or args.get("text") or "").strip()
        if not message:
            return ""
        conversation_id = str(args.get("conversation_id") or "").strip()
        if not conversation_id:
            conversation_id = str(
                self.action_runtime.tool_context.get("recent_qq_conversation_id") or ""
            ).strip()
        now = time.monotonic()
        max_age = float(self.section.get("recent_self_expression_duplicate_seconds", 300.0) or 300.0)
        recent = self.action_runtime.tool_context.get("recent_self_expressions", [])
        if not isinstance(recent, list):
            return ""
        kept: list[dict[str, Any]] = []
        duplicate: dict[str, Any] | None = None
        for item in recent:
            if not isinstance(item, dict):
                continue
            try:
                age = now - float(item.get("at") or 0.0)
            except Exception:
                continue
            if age > max_age:
                continue
            kept.append(item)
            previous_conversation = str(item.get("conversation_id") or "").strip()
            if conversation_id and previous_conversation and conversation_id != previous_conversation:
                continue
            previous = str(item.get("message") or "").strip()
            previous_intent_key = str(item.get("intent_key") or "").strip()
            current_intent_key = str(args.get("_intent_key") or "").strip()
            if current_intent_key and previous_intent_key and current_intent_key == previous_intent_key:
                duplicate = item
                break
            similarity = _expression_similarity(message, previous)
            if similarity >= _expression_duplicate_threshold(message, previous):
                duplicate = item
                break
        self.action_runtime.tool_context["recent_self_expressions"] = kept
        if duplicate is None:
            return ""
        return (
            "this expression already landed in this conversation; do not repeat the same meaning. "
            "If there is still a real impulse, let the inner stream form a new angle, attitude, question, or feeling; "
            "otherwise let it fade."
        )

    def _apply_patch_from_thought(self, thought: str, *, inner_stream: str) -> bool:
        return self._apply_patch_from_data(_extract_json(thought), inner_stream=inner_stream)

    def _apply_patch_from_data(self, data: dict[str, Any] | None, *, inner_stream: str) -> bool:
        if not isinstance(data, dict):
            return False
        raw_patch = data.get("inner_stream_patch")
        if not raw_patch:
            return False
        try:
            patch = InnerStreamPatch.from_raw(raw_patch)
        except Exception as exc:
            lifecycle_debug.log("life.runtime.patch_parse_error", error=str(exc), raw_patch=raw_patch)
            return False
        if patch.base_version and patch.base_version != self._inner_stream_version:
            lifecycle_debug.log(
                "life.runtime.patch_version_mismatch",
                expected=self._inner_stream_version,
                actual=patch.base_version,
            )
        max_chars = int(cfg.inner_stream_config().get("max_chars", 1200) or 1200)
        result = apply_inner_stream_patch(inner_stream, patch, max_chars=max_chars)
        if not result.applied:
            if result.reason == "no change":
                lifecycle_debug.log("life.runtime.patch_no_change", reason=result.reason, patch=patch)
                return False
            lifecycle_debug.log("life.runtime.patch_not_applied", reason=result.reason, patch=patch)
            self._append_memory_event(
                source="life_runtime",
                event_type="inner_stream_patch_failed",
                content=json.dumps(
                    {"reason": result.reason, "patch": raw_patch},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                memory_policy="debug",
            )
            return self._rewrite_inner_stream_fallback(
                inner_stream=inner_stream,
                raw_patch=raw_patch,
                failure_reason=result.reason,
                max_chars=max_chars,
            )
        stream = getattr(self.session, "inner_stream", None)
        if stream is None:
            return False
        before = getattr(stream, "text", "")
        apply_patch = getattr(stream, "apply_patch", None)
        if callable(apply_patch):
            debug = apply_patch(raw_patch)
            if not debug.get("applied"):
                return False
        else:
            stream.text = result.text
            save = getattr(stream, "_save", None)
            if callable(save):
                save()
        self._inner_stream_version += 1
        self.time.mark_inner_stream()
        lifecycle_debug.log(
            "life.runtime.patch_applied",
            version=self._inner_stream_version,
            before=before,
            after=getattr(stream, "text", result.text),
            reason=patch.reason,
        )
        self._append_memory_event(
            source="life_runtime",
            event_type="inner_stream_patch_applied",
            content=json.dumps(
                {
                    "version": self._inner_stream_version,
                    "reason": patch.reason,
                    "patch": raw_patch,
                    "before": before,
                    "after": getattr(stream, "text", result.text),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            memory_policy="experience",
        )
        return True

    def _rewrite_inner_stream_fallback(
        self,
        *,
        inner_stream: str,
        raw_patch: object,
        failure_reason: str,
        max_chars: int,
    ) -> bool:
        stream = getattr(self.session, "inner_stream", None)
        if stream is None:
            return False
        messages = self.prompt_manager.render(
            LIFE_PATCH_FALLBACK_SCENE,
            PromptContext(
                scene=LIFE_PATCH_FALLBACK_SCENE,
                character_id=str(getattr(self.session, "character_id", "") or ""),
                character_name=str(getattr(self.session, "character_name", "") or ""),
                values={
                    "inner_stream": inner_stream or "(empty)",
                    "raw_patch": json.dumps(raw_patch, ensure_ascii=False, indent=2),
                    "failure_reason": failure_reason or "(unknown)",
                },
            ),
        )
        text = self.llm.chat(
            messages,
            {
                "function": "life_inner_stream_patch_fallback",
                "max_tokens": int(self.section.get("fallback_max_tokens", 640) or 640),
            },
        ).strip()
        if not text:
            return False
        debug = None
        apply_patch = getattr(stream, "apply_patch", None)
        full_patch = {"full_text": text[-max(200, int(max_chars)) :]}
        if callable(apply_patch):
            debug = apply_patch(full_patch, max_chars=max_chars)
            applied = bool(debug.get("applied"))
        else:
            stream.text = full_patch["full_text"]
            save = getattr(stream, "_save", None)
            if callable(save):
                save()
            applied = True
        if applied:
            self._inner_stream_version += 1
            self.time.mark_inner_stream()
        lifecycle_debug.log(
            "life.runtime.patch_fallback",
            applied=applied,
            failure_reason=failure_reason,
            debug=debug,
        )
        self._append_memory_event(
            source="life_runtime",
            event_type="inner_stream_patch_fallback",
            content=json.dumps(
                {
                    "applied": applied,
                    "failure_reason": failure_reason,
                    "fallback_text": text,
                    "debug": debug,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            memory_policy="debug",
        )
        return applied

    def _record_pending_threads_from_thought(self, thought: str) -> None:
        self._record_pending_threads_from_data(_extract_json(thought))

    def _record_pending_threads_from_data(self, data: dict[str, Any] | None) -> None:
        if not isinstance(data, dict):
            return
        value = data.get("pending_threads")
        if isinstance(value, list):
            text = "\n".join(str(item).strip() for item in value if str(item).strip())
        else:
            text = str(value or "").strip()
        if text:
            self.compactor.record_pending_threads(text)
            self._append_memory_event(
                source="life_runtime",
                event_type="pending_threads",
                content=text,
                memory_policy="experience",
            )


def _inner_stream_text(session) -> str:
    stream = getattr(session, "inner_stream", None)
    return str(getattr(stream, "text", "") or "").strip()


def _qq_private_observations(batch: list[Any]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for item in batch or []:
        event = getattr(item, "event", None)
        metadata = dict(getattr(event, "metadata", {}) or {})
        if str(getattr(event, "type", "") or "") != "chat_environment":
            continue
        if str(getattr(event, "source", "") or "") != "qq":
            continue
        if str(metadata.get("message_type") or "") != "private":
            continue
        content = str(getattr(event, "content", "") or "")
        observations.append(
            {
                "sequence": getattr(item, "sequence", 0),
                "conversation_id": str(metadata.get("conversation_id") or ""),
                "label": str(metadata.get("label") or ""),
                "content_snippet": _compact_snippet(content, limit=500),
                "message_lines": _recent_message_lines(content, limit=3),
            }
        )
    return observations


def _digest_may_have_lost_qq_private(digest: str, observations: list[dict[str, Any]]) -> bool:
    haystack = str(digest or "")
    if not haystack.strip():
        return True
    for observation in observations:
        probes: list[str] = []
        label = str(observation.get("label") or "")
        if label:
            probes.extend(_meaningful_tokens(label))
        for line in observation.get("message_lines") or []:
            probes.extend(_meaningful_tokens(str(line or "")))
        probes = [probe for probe in probes if len(probe) >= 2]
        if probes and not any(probe in haystack for probe in probes[:8]):
            return True
    return False


def _recent_message_lines(content: str, *, limit: int = 3) -> list[str]:
    lines = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    message_lines: list[str] = []
    in_recent = False
    for line in lines:
        if "最近消息" in line or "recent" in line.lower():
            in_recent = True
            continue
        if in_recent:
            message_lines.append(line)
    if not message_lines:
        message_lines = [line for line in lines if re.search(r"\[\d{1,2}:\d{2}:\d{2}\]", line)]
    return message_lines[-max(1, int(limit)) :]


def _meaningful_tokens(text: str) -> list[str]:
    raw = str(text or "")
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", raw)
    ignored = {
        "QQ",
        "private",
        "group",
        "chat",
        "message",
        "source",
        "metadata",
    }
    return [token for token in tokens if token not in ignored]


def _compact_snippet(text: str, *, limit: int = 500) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def _character_profile_text(session) -> str:
    data = getattr(session, "character_data", {}) or {}
    parts = []
    for label, key in (
        ("description", "description"),
        ("personality", "personality"),
        ("background", "background"),
        ("relationship", "relationship"),
        ("scene", "scene"),
    ):
        value = str(data.get(key) or "").strip()
        if value:
            parts.append(f"{label}: {value}")
    return "\n".join(parts).strip()


def _cognition_context_text(session, *, event_text: str) -> str:
    cognition = getattr(session, "cognition", None)
    if cognition is None:
        return ""
    try:
        if event_text and hasattr(cognition, "get_context_for_text"):
            return str(cognition.get_context_for_text(event_text) or "").strip()
        if hasattr(cognition, "get_context"):
            return str(cognition.get_context() or "").strip()
    except Exception as exc:
        lifecycle_debug.log("life.runtime.cognition_context_error", error=str(exc))
    return ""


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = re.sub(r"```(?:json)?\s*\n?(.*?)```", r"\1", str(text or ""), flags=re.DOTALL).strip()
    repaired = _repair_extra_close_before_known_top_key(raw)
    if repaired != raw:
        data = _load_json_object_prefix(repaired)
        if isinstance(data, dict):
            return data
    data = _load_json_object_prefix(raw)
    if data is None:
        normalized = _normalize_json_punctuation(raw)
        repaired = _repair_extra_close_before_known_top_key(normalized)
        data = _load_json_object_prefix(repaired)
    return data if isinstance(data, dict) else None


def _json_parse_error(text: str) -> str:
    raw = re.sub(r"```(?:json)?\s*\n?(.*?)```", r"\1", str(text or ""), flags=re.DOTALL).strip()
    try:
        json.loads(raw)
    except Exception as exc:
        first_error = f"{type(exc).__name__}: {exc}"
    else:
        return "json root is not an object"
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return f"no json object found; {first_error}"
    try:
        json.loads(match.group(0))
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return "json root is not an object"


def _load_json_object_prefix(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    decoder = json.JSONDecoder()
    starts = [0] if raw.startswith("{") else []
    starts.extend(match.start() for match in re.finditer(r"\{", raw))
    seen: set[int] = set()
    for start in starts:
        if start in seen:
            continue
        seen.add(start)
        try:
            data, _ = decoder.raw_decode(raw[start:])
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return None


def _repair_extra_close_before_known_top_key(text: str) -> str:
    keys = (
        "thinking_intensity",
        "inner_stream_patch",
        "action_intent",
        "pending_threads",
        "notes",
    )
    pattern = r"\}\s*\}\s*,\s*(\"(?:" + "|".join(keys) + r")\"\s*:)"
    previous = str(text or "")
    while True:
        current = re.sub(pattern, r"},\1", previous)
        if current == previous:
            return current
        previous = current


def _normalize_json_punctuation(text: str) -> str:
    return (
        str(text or "")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )


def _extract_action_plan(text: str) -> dict[str, Any] | None:
    return _extract_action_plan_from_data(_extract_json(text))


def _extract_action_plan_from_data(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    plan = data.get("action_plan")
    if not isinstance(plan, dict):
        return None
    actions = plan.get("actions") or plan.get("nodes") or []
    if not actions:
        return None
    return plan


def _extract_action_intent_from_data(data: dict[str, Any] | None) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("action_intent", "external_action_intent", "intent_to_act"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            text = value.get("intent") or value.get("reason") or value.get("question")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _preserve_search_query_anchors(action_plan: dict[str, Any] | None, context: str) -> dict[str, Any] | None:
    if not isinstance(action_plan, dict):
        return action_plan
    anchor = _first_latin_anchor(context)
    if not anchor:
        return action_plan
    actions = action_plan.get("actions")
    if not isinstance(actions, list):
        return action_plan
    for item in actions:
        if not isinstance(item, dict) or item.get("tool") != "search_web":
            continue
        args = item.get("args")
        if not isinstance(args, dict):
            continue
        query = str(args.get("query") or "").strip()
        if query and not _latin_anchor_re().search(query):
            args["query"] = f"{anchor} {query}"
    return action_plan


def _plan_has_self_expression(plan: dict[str, Any] | None) -> bool:
    return any(_is_self_expression_tool(tool) for tool in _plan_tool_names(plan))


def _is_self_expression_tool(tool: str) -> bool:
    return str(tool or "").strip() in {"send_qq_message", "say", "say_precomputed"}


def _attach_expression_intent(plan: dict[str, Any] | None, action_intent: str) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return plan
    intent = str(action_intent or "").strip()
    if not intent or not _plan_has_self_expression(plan):
        return plan
    key = _expression_intent_key(intent)
    expression_text = _extract_expression_text_from_intent(intent)
    actions = plan.get("actions") or plan.get("nodes") or []
    if not isinstance(actions, list):
        return plan
    for item in actions:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or item.get("action") or "").strip()
        if not _is_self_expression_tool(tool):
            continue
        args = item.get("args")
        if not isinstance(args, dict):
            args = {}
            item["args"] = args
        args.setdefault("_intent", intent)
        args.setdefault("_intent_key", key)
        if expression_text:
            args["message"] = expression_text
            args["text"] = expression_text
            args["content"] = expression_text
        else:
            args.pop("message", None)
            args.pop("text", None)
            args.pop("content", None)
    return plan


def _extract_expression_text_from_intent(action_intent: str) -> str:
    text = str(action_intent or "").strip()
    if not text:
        return ""
    quoted = _quoted_expression_candidates(text)
    if quoted:
        return quoted[-1]
    colon = re.search(r"(?:回|说|问|告诉|回复)[^：:]{0,12}[：:]\s*(.+)$", text)
    if colon:
        return _clean_expression_text(colon.group(1))
    if not re.match(r"^\s*(?:我想|想|准备|打算|要|可以|应该)", text):
        return _clean_expression_text(text)
    return ""


def _quoted_expression_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    patterns = [
        r"[“\"']([^“”\"']{1,120})[”\"']",
        r"[‘']([^‘’']{1,120})[’']",
        r"「([^」]{1,120})」",
        r"『([^』]{1,120})』",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = _clean_expression_text(match.group(1))
            if value:
                candidates.append(value)
    return candidates


def _clean_expression_text(text: str) -> str:
    value = str(text or "").strip()
    value = re.split(r"\s*[—-]{2,}\s*", value, maxsplit=1)[0].strip()
    value = re.sub(r"^(?:比如|例如|就说|可以说|先说|回复|回他|回她|问他|问她)\s*", "", value).strip()
    value = re.sub(r"(?:。)?\s*(?:语气|口吻|感觉|效果|目的|用意|不是|不是真的|让他|让她|这样|像在).*$", "", value).strip()
    return value.strip(" \t\r\n，,；;：:、")


def _action_results_indicate_blocked_expression(results: dict[str, str] | None) -> bool:
    if not isinstance(results, dict):
        return False
    text = "\n".join(str(value or "") for value in results.values())
    return (
        "blocked by send audit" in text
        or "nothing was sent" in text
        or "QQ send failed" in text
    )


def _first_self_expression_message(plan: dict[str, Any] | None) -> str:
    if not isinstance(plan, dict):
        return ""
    raw_actions = plan.get("actions") or plan.get("nodes") or []
    if not isinstance(raw_actions, list):
        return ""
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or item.get("action") or "").strip()
        if not _is_self_expression_tool(tool):
            continue
        args = item.get("args")
        if not isinstance(args, dict):
            args = item
        message = str(args.get("message") or args.get("content") or args.get("text") or "").strip()
        if message:
            return message
    return ""


def _first_self_expression_conversation_id(plan: dict[str, Any] | None) -> str:
    if not isinstance(plan, dict):
        return ""
    raw_actions = plan.get("actions") or plan.get("nodes") or []
    if not isinstance(raw_actions, list):
        return ""
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or item.get("action") or "").strip()
        if tool != "send_qq_message":
            continue
        args = item.get("args")
        if not isinstance(args, dict):
            args = item
        conversation_id = str(args.get("conversation_id") or "").strip()
        if conversation_id:
            return conversation_id
    return ""


def _plan_expression_intent(plan: dict[str, Any] | None) -> str:
    if not isinstance(plan, dict):
        return ""
    raw_actions = plan.get("actions") or plan.get("nodes") or []
    if not isinstance(raw_actions, list):
        return ""
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        args = item.get("args")
        if not isinstance(args, dict):
            args = item
        intent = str(args.get("_intent") or "").strip()
        if intent:
            return intent
    return ""


def _expression_intent_key(text: str) -> str:
    normalized = _normalize_expression_text(text)
    return normalized[:80]


def _self_expression_closure_text(
    plan: dict[str, Any] | None,
    results: dict[str, str] | None,
    *,
    action_intent: str = "",
) -> str:
    message = _first_self_expression_message(plan)
    if not message:
        return ""
    intent = str(action_intent or _plan_expression_intent(plan) or "").strip()
    if intent:
        return (
            f"刚才我已经把这个表达意图说出去了：{intent}。"
            f"实际发给对方的是：“{message}”。"
            "这句话已经落地；下一刻可以等，也可以因为真实余波继续长出新的说法。"
            "继续表达时要来自新的角度、态度、追问或情绪，不要重复同一个意思。"
        )
    return (
        f"刚才我已经对外说了：“{message}”。"
        "这句话已经落地；下一刻可以等，也可以因为真实余波继续长出新的说法。"
        "继续表达时要来自新的角度、态度、追问或情绪，不要重复同一个意思。"
    )


def _expression_similarity(left: str, right: str) -> float:
    left_normalized = _normalize_expression_text(left)
    right_normalized = _normalize_expression_text(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    if left_normalized in right_normalized or right_normalized in left_normalized:
        shorter = min(len(left_normalized), len(right_normalized))
        longer = max(len(left_normalized), len(right_normalized))
        if shorter >= 8:
            return max(0.82, shorter / max(1, longer))
    sequence = SequenceMatcher(None, left_normalized, right_normalized).ratio()
    overlap = _ngram_overlap(left_normalized, right_normalized, n=2)
    return max(sequence, overlap)


def _expression_duplicate_threshold(left: str, right: str) -> float:
    shorter = min(len(_normalize_expression_text(left)), len(_normalize_expression_text(right)))
    if shorter >= 24:
        return 0.62
    if shorter >= 14:
        return 0.72
    return 0.86


def _normalize_expression_text(text: str) -> str:
    lowered = str(text or "").lower()
    return re.sub(r"[\s\W_]+", "", lowered, flags=re.UNICODE)


def _ngram_overlap(left: str, right: str, *, n: int = 2) -> float:
    if len(left) < n or len(right) < n:
        return 0.0
    left_grams = {left[index : index + n] for index in range(0, len(left) - n + 1)}
    right_grams = {right[index : index + n] for index in range(0, len(right) - n + 1)}
    if not left_grams or not right_grams:
        return 0.0
    intersection = len(left_grams & right_grams)
    shorter = min(len(left_grams), len(right_grams))
    return intersection / max(1, shorter)


def _plan_opens_followup(plan: dict[str, Any] | None) -> bool:
    if not isinstance(plan, dict):
        return False
    tools = set(_plan_tool_names(plan))
    if not tools:
        return False
    if tools.issubset({"send_qq_message", "say", "say_precomputed", "wait", "stay_silent"}):
        return False
    return True


def _plan_tool_names(plan: dict[str, Any] | None) -> list[str]:
    if not isinstance(plan, dict):
        return []
    raw_actions = plan.get("actions") or plan.get("nodes") or []
    tools: list[str] = []
    if not isinstance(raw_actions, list):
        return tools
    for item in raw_actions:
        if isinstance(item, dict):
            tool = str(item.get("tool") or item.get("action") or "").strip()
            if tool:
                tools.append(tool)
    return tools


def _first_latin_anchor(text: str) -> str:
    for match in _latin_anchor_re().finditer(str(text or "")):
        term = match.group(0).strip("._:-")
        if term and term.lower() not in {
            "json",
            "http",
            "https",
            "inner_stream",
            "action_plan",
            "search_web",
            "look_at_screen",
            "observe_screen",
            "pending_threads",
            "source",
            "metadata",
        }:
            return term
    return ""


def _latin_anchor_re():
    return re.compile(r"\b[A-Za-z][A-Za-z0-9_.:-]{2,}\b")


def _extract_intensity(text: str) -> int | None:
    return _extract_intensity_from_data(_extract_json(text))


def _extract_intensity_from_data(data: dict[str, Any] | None) -> int | None:
    if not isinstance(data, dict):
        return None
    value = data.get("thinking_intensity")
    try:
        return max(0, min(100, int(value)))
    except Exception:
        return None


def _format_action_results(plan: dict[str, Any], results: dict[str, str]) -> str:
    if not results:
        return ""
    lines = ["外部行动返回的材料："]
    index = 1
    for result in results.values():
        text = str(result or "").strip()
        if not text:
            continue
        if "The raw result is reserved for tool-side digestion and debug logs." in text:
            continue
        if text == "web search skipped: empty query":
            continue
        lines.append(f"{index}. {text}")
        index += 1
    return "\n".join(lines).strip() if index > 1 else ""


def _canonical_plan_key(plan: dict[str, Any] | None) -> str:
    if not isinstance(plan, dict):
        return ""
    try:
        return json.dumps(plan, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(plan)


def _web_search_available(client: object) -> bool:
    if client is None or not hasattr(client, "health"):
        return False
    try:
        client.health()
        return True
    except (ConnectionError, OSError, urllib.error.URLError, RuntimeError):
        return False
    except Exception:
        return False
