"""Autonomous life runtime skeleton."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import urllib.error

from kokoro.action import runtime as action_runtime_mod
from kokoro.action import tool_spec
from kokoro.action import tools as action_tools
from kokoro.action.plan import ActionPlan, execute_action_plan
from kokoro.action.tools import search_web as search_web_tool
from kokoro.core import config as cfg
from kokoro.core import input_events
from kokoro.core import lifecycle_debug
from kokoro.life.context_compactor import ContextCompactor
from kokoro.life.event_pool import InformationPool
from kokoro.life.local_thinking import LocalThinking
from kokoro.life.stream_patch import InnerStreamPatch, apply_inner_stream_patch
from kokoro.life.time_awareness import TimeAwareness
from kokoro.prompt import PromptContext, PromptManager
from kokoro.prompt.contracts import LIFE_JSON_REPAIR_SCENE, LIFE_PATCH_FALLBACK_SCENE, LIFE_TICK_SCENE
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
        self.prompt_manager = PromptManager()
        self.tool_registry = tool_spec.ActionToolRegistry()
        action_tools.register_all(self.tool_registry)
        self.tool_prompt_specs = discover_tool_prompt_specs(self.root / "kokoro" / "action" / "tools")
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
        lifecycle_debug.log(
            "life.runtime.init",
            character_id=getattr(session, "character_id", ""),
            enabled=self.enabled,
        )

    def _create_action_runtime(self):
        search_section = cfg.inner_stream_search_config()
        search_client = search_web_tool.create_client(search_section)
        merge_window = self.section.get("result_merge_window_seconds", 1.0)
        if merge_window is None:
            merge_window = 1.0
        return action_runtime_mod.ActionRuntime(
            session=self.session,
            handlers={},
            registry=self.tool_registry,
            tool_context={
                "tool_timeout": float(self.section.get("tool_timeout", 45.0) or 45.0),
                "character_id": getattr(self.session, "character_id", "default"),
                "memory_system": getattr(self.session, "memory_system", None),
                "memory_backend": getattr(self.session, "memory_backend", None),
                "web_search_client": search_client,
                "search_max_results": int(search_section.get("max_results", 5) or 5),
                "search_max_event_chars": int(search_section.get("max_event_chars", 6000) or 6000),
            },
            merge_window_seconds=float(merge_window),
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
        if callable(start_lifecycle):
            memory_section = dict(self.section.get("memory_lifecycle", {}) or {})
            start_lifecycle(
                interval_seconds=float(memory_section.get("interval_seconds", 20.0) or 20.0),
                max_batches_per_wake=int(memory_section.get("max_batches_per_wake", 3) or 3),
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
        time_context = self.time.render()
        digest = self.compactor.compact_once(time_context=time_context, inner_stream=inner_stream)
        event_text = self.pool.format_batch(batch, max_chars=int(self.section.get("batch_max_chars", 4000) or 4000))
        thought = self._think(
            inner_stream=inner_stream,
            time_context=time_context,
            digest=digest,
            event_text=event_text,
        )
        data = _extract_json(thought)
        self._write_prompt_trace_result(llm_raw=thought, parsed=data, tool_plan=None)
        lifecycle_debug.log(
            "life.runtime.thought_raw",
            chars=len(thought or ""),
            text=thought,
            json_ok=isinstance(data, dict),
        )
        if not isinstance(data, dict):
            parse_reason = _json_parse_error(thought)
            lifecycle_debug.log(
                "life.runtime.thought_parse_failed",
                reason=parse_reason,
                text=thought,
            )
            repaired = self._repair_json_thought(thought, parse_reason=parse_reason)
            if repaired:
                repaired_data = _extract_json(repaired)
                lifecycle_debug.log(
                    "life.runtime.thought_repair_raw",
                    chars=len(repaired),
                    text=repaired,
                    json_ok=isinstance(repaired_data, dict),
                )
                if isinstance(repaired_data, dict):
                    thought = repaired
                    data = repaired_data
                    lifecycle_debug.log("life.runtime.thought_repair_applied")
                else:
                    lifecycle_debug.log(
                        "life.runtime.thought_repair_failed",
                        reason=_json_parse_error(repaired),
                    )
        self.time.mark_llm_thought()
        patch_applied = self._apply_patch_from_data(data, inner_stream=inner_stream)
        self._record_pending_threads_from_data(data)
        action_plan = _extract_action_plan_from_data(data)
        self._write_prompt_trace_result(llm_raw=thought, parsed=data, tool_plan=action_plan)
        intensity = _extract_intensity_from_data(data)
        action_plan_status = ""
        action_plan_error = ""
        if action_plan:
            action_plan_status, action_plan_error = self._execute_action_plan(action_plan)
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

    def _execute_action_plan(self, raw_plan: dict[str, Any]) -> tuple[str, str]:
        if self.action_runtime is None:
            lifecycle_debug.log("life.runtime.action_plan.no_runtime", action_plan=raw_plan)
            return "rejected", "action runtime is not available"
        try:
            plan = ActionPlan.from_dict(raw_plan)
            validation_error = self._validate_action_plan(plan)
            if validation_error:
                lifecycle_debug.log(
                    "life.runtime.action_plan.rejected",
                    plan=plan,
                    error=validation_error,
                )
                return "rejected", validation_error
            lifecycle_debug.log("life.runtime.action_plan.execute", plan=plan)
            results = execute_action_plan(plan, self.action_runtime)
            lifecycle_debug.log("life.runtime.action_plan.done", plan=plan, results=results)
            return "executed", ""
        except Exception as exc:
            lifecycle_debug.log("life.runtime.action_plan.error", action_plan=raw_plan, error=str(exc))
            return "error", str(exc)

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
        cognition_context = _cognition_context_text(self.session, event_text=event_text)
        memory_context = self._memory_context(event_text=event_text, inner_stream=inner_stream, digest=digest)
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
                "character_profile": character_profile or "(none)",
                "cognition_context": cognition_context or "(none)",
                "memory_context": memory_context or "(none)",
                "inner_stream": inner_stream or "(empty)",
                "inner_stream_version": self._inner_stream_version,
                "time_context": time_context or "(none)",
                "context_digest": digest or "(none)",
                "tool_capabilities": self._tool_capabilities_text(),
                "event_batch": event_text or "(no new external event; this can be time passing or continued thinking)",
                "pending_threads": self.compactor.pending_threads() or "(none)",
                "tool_results_digest": self.compactor.tool_results_digest() or "(none)",
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
            },
        )

    def _memory_context(self, *, event_text: str, inner_stream: str, digest: str) -> str:
        memory_system = getattr(self.session, "memory_system", None)
        if memory_system is not None:
            try:
                return str(
                    memory_system.default_context(
                        event_text=event_text,
                        inner_stream=inner_stream,
                        recent_digest=digest,
                        pending_threads=self.compactor.pending_threads(),
                    )
                    or ""
                ).strip()
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
        prompt_catalog = render_tool_catalog(self.tool_prompt_specs, actions)
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
        if context.get("retire_sticker_store") is None:
            unavailable.add("retire_sticker")
        memory_system = context.get("memory_system")
        memory_backend = context.get("memory_backend")
        if memory_system is None and (memory_backend is None or not getattr(memory_backend, "ready", False)):
            unavailable.update({"search_memory", "save_to_memory"})
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
            if missing:
                return f"action {node.tool} missing required args: {', '.join(dict.fromkeys(missing))}"
        return ""

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
            lifecycle_debug.log("life.runtime.patch_not_applied", reason=result.reason, patch=patch)
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


def _inner_stream_text(session) -> str:
    stream = getattr(session, "inner_stream", None)
    return str(getattr(stream, "text", "") or "").strip()


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
    try:
        data = json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except Exception:
            return None
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
