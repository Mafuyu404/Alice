from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kokoro.core import chat_session
from kokoro.core import character as character_mod
from kokoro.core import config as cfg
from kokoro.core import input_events
from kokoro.core import lifecycle_debug
from kokoro.core import memory as memory_mod
from kokoro.action.tools import search_web as search_web_tool
from kokoro.life.stream_patch import InnerStreamPatch, apply_inner_stream_patch


class DebugInnerStream:
    def __init__(self, path: Path, text: str = "") -> None:
        self.path = path
        self.text = text
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._save()

    def get_context(self) -> str:
        return self.text.strip()

    def apply_patch(self, raw_patch, *, max_chars=None):
        result = apply_inner_stream_patch(
            self.text,
            InnerStreamPatch.from_raw(raw_patch),
            max_chars=int(max_chars or 1600),
        )
        if result.applied:
            self.text = result.text
            self._save()
        return {"applied": result.applied, "reason": result.reason, "after": self.text}

    def _save(self) -> None:
        self.path.write_text(self.text.strip() + "\n", encoding="utf-8")


class ScriptedLifeLlm:
    """Deterministic LLM for validating the LifeRuntime skeleton."""

    def __init__(self) -> None:
        self.life_tick_count = 0

    def chat(self, messages, options=None):
        options = dict(options or {})
        function = str(options.get("function") or "")
        lifecycle_debug.log(
            "life_debug.scripted_llm.call",
            function=function,
            message_count=len(messages),
            last_message=messages[-1] if messages else {},
        )
        if function == "life_context_compact":
            return "A compact debug timeline: the life runtime is receiving events, sensing time, and keeping unfinished threads visible."
        if function == "life_inner_stream_patch_fallback":
            return "I notice the debug run continuing, keep the time thread in view, and stay ready to use tools when they help."
        self.life_tick_count += 1
        if self.life_tick_count == 1:
            return json.dumps(
                {
                    "thinking_intensity": 76,
                    "inner_stream_patch": {
                        "base_version": 0,
                        "patches": [
                            {
                                "op": "append",
                                "text": "The debug run has started; I can feel time becoming part of the situation instead of a background tick.",
                            }
                        ],
                        "reason": "The first life tick absorbed the debug event and time context.",
                    },
                    "action_plan": {
                        "reason": "Check time as a benign tool action while keeping the stream active.",
                        "actions": [
                            {
                                "id": "time_check",
                                "tool": "get_current_time",
                                "args": {},
                                "parallel": True,
                                "result_policy": "feed_back",
                            }
                        ],
                    },
                    "notes": "first deterministic life tick",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "thinking_intensity": 58,
                "inner_stream_patch": {
                    "base_version": 0,
                    "patches": [
                        {
                            "op": "append",
                            "text": "The loop continues after the tool result; I keep the unfinished diagnostic thread compressed but available.",
                        }
                    ],
                    "reason": "The runtime kept cycling after feedback.",
                },
                "notes": "continued deterministic life tick",
            },
            ensure_ascii=False,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a LifeRuntime debug trace for one character.")
    parser.add_argument("--character", default="lerwa")
    parser.add_argument("--ticks", type=int, default=3)
    parser.add_argument("--duration-seconds", type=float, default=0.0, help="Run the LifeRuntime background loop for this many seconds instead of manual ticks.")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--real-llm", action="store_true", help="Use configured local/remote LLM instead of deterministic scripted responses.")
    parser.add_argument("--llm-model", default="", help="Override LifeRuntime local thinking model for --real-llm.")
    parser.add_argument("--llm-url", default="", help="Override LifeRuntime local thinking base URL for --real-llm.")
    parser.add_argument("--api-style", default="", choices=["", "auto", "ollama", "openai"], help="Override local thinking API style.")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    mode = "real" if args.real_llm else "scripted"
    out_dir = Path(args.out_dir or Path("test_runs") / f"life_runtime_debug_{args.character}_{mode}_{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = out_dir / "lifecycle_trace.jsonl"
    os.environ["KOKORO_LIFECYCLE_TRACE"] = str(trace_path)
    full_config = cfg.load()
    web_search_runtime = _start_web_search_runtime(full_config, out_dir=out_dir)

    section = {
        "enabled": True,
        "primary": True,
        "idle_tick_seconds": 0.2,
        "min_tick_seconds": 0.05,
        "max_tick_seconds": 0.5,
        "batch_max_events": 16,
        "batch_max_chars": 4000,
        "context_max_chars": 4000,
        "tick_max_tokens": 640,
        "fallback_max_tokens": 640,
        "result_merge_window_seconds": 0.0,
        "local_thinking": {
            "enabled": True,
            **({"model": args.llm_model} if args.llm_model else {}),
            **({"base_url": args.llm_url} if args.llm_url else {}),
            **({"api_style": args.api_style} if args.api_style else {}),
        },
    }
    characters = character_mod.load()
    if args.character not in characters:
        raise KeyError(args.character)
    backend = memory_mod.NoMemoryBackend()
    session = chat_session.ChatSession(
        character_id=args.character,
        character_data=characters[args.character],
        memory_backend=backend,
        user_name=cfg.user_name(),
        inner_stream=DebugInnerStream(out_dir / "characters" / args.character / "inner_stream.txt"),
        inner_stream_loop=object(),
    )
    session.inner_stream_loop = None

    from kokoro.life import LifeRuntime

    llm = None if args.real_llm else ScriptedLifeLlm()
    runtime = LifeRuntime(session=session, section=section, llm=llm, root=out_dir)
    session.life_runtime = runtime
    session.inner_stream_loop = None
    session.autonomous_step = None
    session.event_bus = input_events.InputEventBus()
    session.event_bus.subscribe(runtime.submit)

    lifecycle_debug.log(
        "life_debug.start",
        character=args.character,
        ticks=args.ticks,
        duration_seconds=args.duration_seconds,
        out_dir=str(out_dir),
        scripted=not args.real_llm,
    )
    runtime.submit(
        input_events.build_text_event(
            "LifeRuntime debug start: absorb this event, notice elapsed time, and decide whether a tool helps.",
            source="life_runtime_debug",
            metadata={"debug_run": True, "phase": "start"},
            priority="high",
            lifetime="session",
        )
    )
    results = []
    error = ""
    try:
        if args.duration_seconds and args.duration_seconds > 0:
            runtime.start()
            deadline = time.monotonic() + float(args.duration_seconds)
            while time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.05, deadline - time.monotonic())))
        else:
            for index in range(max(1, args.ticks)):
                try:
                    result = runtime.tick_once(force=index > 0)
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    lifecycle_debug.log("life_debug.tick_error", tick=index + 1, error=error)
                    break
                results.append(_tick_result_summary(index + 1, result))
                time.sleep(0.05)
    finally:
        runtime.stop(wait=True, timeout=5.0)
        _stop_runtime(web_search_runtime)
        close = getattr(backend, "close", None)
        if callable(close):
            close()

    if args.duration_seconds and args.duration_seconds > 0:
        results = _tick_summaries_from_trace(trace_path)

    summary = {
        "character": args.character,
        "scripted": not args.real_llm,
        "error": error,
        "duration_seconds": float(args.duration_seconds or 0.0),
        "ticks": results,
        "trace_counts": _trace_counts(trace_path),
        "trace_path": str(trace_path),
        "inner_stream": getattr(getattr(session, "inner_stream", None), "text", ""),
        "context_digest": runtime.compactor.recent_digest(),
        "pending_threads": runtime.compactor.pending_threads(),
        "tool_results_digest": runtime.compactor.tool_results_digest(),
    }
    (out_dir / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lifecycle_debug.log("life_debug.done", summary=summary)
    print(str(out_dir))
    return 0


def _tick_result_summary(index: int, result) -> dict:
    return {
        "tick": index,
        "processed_events": result.processed_events,
        "patch_applied": result.patch_applied,
        "thinking_intensity": result.thinking_intensity,
        "action_plan": result.action_plan,
        "action_plan_status": result.action_plan_status,
        "action_plan_error": result.action_plan_error,
    }


def _start_web_search_runtime(config: dict, *, out_dir: Path):
    runtime = search_web_tool.start_runtime(config, root=out_dir)
    section = config.get("inner_stream_search") if isinstance(config, dict) else {}
    lifecycle_debug.log(
        "life_debug.transport.web_search",
        enabled=bool(section.get("enabled", False)) if isinstance(section, dict) else False,
        has_runtime=runtime is not None,
        process_started=bool(getattr(runtime, "process", None)),
    )
    return runtime


def _stop_runtime(runtime: object) -> None:
    stop = getattr(runtime, "stop", None)
    if callable(stop):
        stop()


def _tick_summaries_from_trace(trace_path: Path) -> list[dict]:
    summaries: list[dict] = []
    if not trace_path.exists():
        return summaries
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        if record.get("event") != "life.runtime.tick_done":
            continue
        summaries.append(
            {
                "tick": len(summaries) + 1,
                "processed_events": record.get("processed_events"),
                "patch_applied": record.get("patch_applied"),
                "thinking_intensity": record.get("thinking_intensity"),
                "has_action_plan": record.get("has_action_plan"),
                "action_plan_status": record.get("action_plan_status", ""),
                "action_plan_error": record.get("action_plan_error", ""),
            }
        )
    return summaries


def _trace_counts(trace_path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not trace_path.exists():
        return counts
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = str(json.loads(line).get("event") or "")
        except Exception:
            continue
        if event:
            counts[event] = counts.get(event, 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
