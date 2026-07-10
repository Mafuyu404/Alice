from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze a LifeRuntime debug run without semantic classification.")
    parser.add_argument("run_dir", help="Path to a run directory produced by scripts/run_life_runtime_debug.py.")
    parser.add_argument("--write", action="store_true", help="Write continuity_audit.json into the run directory.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    audit = analyze_run(run_dir)
    text = json.dumps(audit, ensure_ascii=False, indent=2, default=str)
    if args.write:
        (run_dir / "continuity_audit.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def analyze_run(run_dir: Path) -> dict[str, Any]:
    summary = _read_json(run_dir / "run_summary.json")
    trace = _read_jsonl(run_dir / "lifecycle_trace.jsonl")
    counts = Counter(str(item.get("event") or "") for item in trace if item.get("event"))
    tick_records = [item for item in trace if item.get("event") == "life.runtime.tick_done"]
    prompt_records = [item for item in trace if item.get("event") == "life.runtime.prompt_rendered"]
    error_records = [
        item
        for item in trace
        if str(item.get("event") or "").endswith("_error") or "error" in str(item.get("event") or "")
    ]
    compaction_audit = _read_compaction_audit(run_dir)
    prompt_trace_dirs = _count_prompt_trace_dirs(run_dir)
    duration_seconds = _duration_from_trace(trace, fallback=float(summary.get("duration_seconds") or 0.0))
    tick_count = len(tick_records)
    tick_rate_per_minute = round((tick_count / duration_seconds) * 60.0, 3) if duration_seconds > 0 else 0.0
    action_executed = sum(1 for item in tick_records if item.get("action_plan_status") == "executed")
    action_rejected = sum(1 for item in tick_records if item.get("action_plan_status") == "rejected")
    action_errors = sum(1 for item in tick_records if item.get("action_plan_status") == "error")
    same_tick_context = int(counts.get("life.runtime.tool_results.same_tick_context", 0))
    tool_result_append = int(counts.get("life.context.tool_result_append", 0))
    prompt_count = len(prompt_records)
    thought_records = [item for item in trace if item.get("event") == "life.runtime.thought_raw"]
    thought_metrics = _thought_progress_metrics(thought_records)
    tool_metrics = _tool_query_metrics(trace)
    memory_store = _memory_store_metrics(run_dir)
    context_pollution = _context_pollution_metrics(run_dir, trace)
    life_tick_llm_calls = sum(
        1
        for item in trace
        if item.get("event") == "life.local_thinking.start" and item.get("function") == "life_tick"
    )
    return {
        "type": "life_runtime_continuity_audit",
        "run_dir": str(run_dir),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "character": summary.get("character", ""),
        "scripted": bool(summary.get("scripted", False)),
        "duration_seconds": duration_seconds,
        "tick_count": tick_count,
        "tick_rate_per_minute": tick_rate_per_minute,
        "processed_event_total": sum(int(item.get("processed_events") or 0) for item in tick_records),
        "patch_applied_count": sum(1 for item in tick_records if item.get("patch_applied")),
        "action_plan": {
            "executed": action_executed,
            "rejected": action_rejected,
            "error": action_errors,
            "with_action_plan": sum(1 for item in tick_records if item.get("has_action_plan")),
        },
        "same_tick_tool_feedback": {
            "context_events": same_tick_context,
            "tool_result_append_events": tool_result_append,
            "present": same_tick_context > 0 or "[same_tick_tool_results]" in str(summary.get("tool_results_digest") or ""),
        },
        "context_continuity": {
            "prompt_rendered": prompt_count,
            "life_tick_llm_calls": life_tick_llm_calls,
            "prompt_trace_dirs": prompt_trace_dirs,
            "compaction_audit_records": len(compaction_audit),
            "pending_threads_chars": len(str(summary.get("pending_threads") or "")),
            "context_digest_chars": len(str(summary.get("context_digest") or "")),
            "inner_stream_chars": len(str(summary.get("inner_stream") or "")),
        },
        "memory_core": {
            "runtime_input_events": int(counts.get("life.event_pool.add", 0)),
            "memory_core_cycles": int(counts.get("life.runtime.memory_core_cycle", 0)),
            "memory_core_deferred": int(counts.get("life.runtime.memory_core_deferred", 0)),
            "memory_candidate_inputs": int(counts.get("life_debug.memory_candidate_event", 0)),
            **memory_store,
        },
        "local_thinking_queue": {
            "queued": int(counts.get("life.local_thinking.queued", 0)),
            "started": int(counts.get("life.local_thinking.start", 0)),
            "done": int(counts.get("life.local_thinking.done", 0)),
            "coalesced": int(counts.get("life.local_thinking.coalesced", 0)),
        },
        "thinking_progress": thought_metrics,
        "tool_query_progress": tool_metrics,
        "context_pollution": context_pollution,
        "errors": {
            "count": len(error_records),
            "events": [str(item.get("event") or "") for item in error_records[:20]],
        },
        "top_trace_events": counts.most_common(30),
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if isinstance(data, dict):
            records.append(data)
    return records


def _duration_from_trace(records: list[dict[str, Any]], *, fallback: float) -> float:
    values = []
    for record in records:
        try:
            values.append(float(record.get("monotonic")))
        except Exception:
            pass
    if len(values) >= 2:
        return round(max(values) - min(values), 3)
    return max(0.0, fallback)


def _read_compaction_audit(run_dir: Path) -> list[dict[str, Any]]:
    character_root = run_dir / "characters"
    if not character_root.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in character_root.glob("*/context/compaction_audit.jsonl"):
        records.extend(_read_jsonl(path))
    return records


def _count_prompt_trace_dirs(run_dir: Path) -> int:
    path = run_dir / "prompt_trace"
    if not path.exists():
        return 0
    return sum(1 for item in path.iterdir() if item.is_dir())


def _thought_progress_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    notes: list[str] = []
    with_patch = 0
    with_action_plan = 0
    with_pending_threads = 0
    for record in records:
        data = _extract_json_object(str(record.get("text") or ""))
        if not isinstance(data, dict):
            continue
        if data.get("inner_stream_patch"):
            with_patch += 1
        if data.get("action_plan"):
            with_action_plan += 1
        if data.get("pending_threads"):
            with_pending_threads += 1
        note = _normalize_note(data.get("notes"))
        if note:
            notes.append(note)
    note_counts = Counter(notes)
    adjacent_repeats = sum(1 for prev, cur in zip(notes, notes[1:]) if prev == cur)
    repeated_notes = sum(count - 1 for count in note_counts.values() if count > 1)
    return {
        "thought_raw": len(records),
        "json_notes": len(notes),
        "unique_notes": len(note_counts),
        "repeated_note_extra": repeated_notes,
        "adjacent_repeated_notes": adjacent_repeats,
        "with_inner_stream_patch": with_patch,
        "with_action_plan": with_action_plan,
        "with_pending_threads": with_pending_threads,
    }


def _tool_query_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    queries: list[str] = []
    for record in records:
        if record.get("event") not in {"life.runtime.action_plan.execute", "tool_registry.prepare.done"}:
            continue
        text = json.dumps(record, ensure_ascii=False, default=str)
        for match in re.finditer(r'"query"\s*:\s*"([^"]+)"', text):
            query = match.group(1).strip()
            if query:
                queries.append(query)
    adjacent_repeats = sum(1 for prev, cur in zip(queries, queries[1:]) if prev == cur)
    return {
        "query_mentions": len(queries),
        "unique_queries": len(set(queries)),
        "adjacent_repeated_queries": adjacent_repeats,
        "queries": list(dict.fromkeys(queries))[:20],
    }


def _memory_store_metrics(run_dir: Path) -> dict[str, Any]:
    stores = list((run_dir / "characters").glob("*/memory/store.sqlite"))
    if not stores:
        return {
            "memory_records": 0,
            "memory_links": 0,
            "recent_memory_summaries": [],
        }
    records = 0
    links = 0
    summaries: list[str] = []
    for path in stores:
        con = None
        try:
            con = sqlite3.connect(path)
            con.row_factory = sqlite3.Row
            records += int(con.execute("select count(*) from memory_records where deleted_at = ''").fetchone()[0])
            links += int(con.execute("select count(*) from memory_links").fetchone()[0])
            rows = con.execute(
                "select created_at, summary from memory_records where deleted_at = '' order by created_at desc limit 8"
            ).fetchall()
            for row in rows:
                summary = str(row["summary"] or "").strip()
                if summary:
                    summaries.append(f"{row['created_at']} {summary}")
        except Exception:
            continue
        finally:
            if con is not None:
                con.close()
    return {
        "memory_records": records,
        "memory_links": links,
        "recent_memory_summaries": summaries[:12],
    }


def _context_pollution_metrics(run_dir: Path, trace: list[dict[str, Any]]) -> dict[str, Any]:
    live_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (run_dir / "characters").glob("*/context/live_timeline.txt")
        if path.exists()
    )
    tool_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (run_dir / "characters").glob("*/context/tool_results_digest.txt")
        if path.exists()
    )
    action_result_inputs = 0
    suppressed_results = 0
    for item in trace:
        if item.get("event") == "chat_session.record_input_event" and "action_result" in json.dumps(item, ensure_ascii=False, default=str):
            action_result_inputs += 1
        if item.get("event") == "action_runtime.result.suppressed":
            suppressed_results += 1
    return {
        "action_result_record_input_events": action_result_inputs,
        "suppressed_action_results": suppressed_results,
        "live_timeline_action_result_mentions": live_text.count('type="action_result"'),
        "tool_results_digest_chars": len(tool_text),
        "tool_results_digest_search_status_mentions": tool_text.count("web search completed")
        + tool_text.count("web search skipped"),
    }


def _normalize_note(value: Any) -> str:
    if isinstance(value, list):
        text = " ".join(str(item or "").strip() for item in value if str(item or "").strip())
    else:
        text = str(value or "").strip()
    return " ".join(text.split())


def _extract_json_object(text: str) -> dict[str, Any] | None:
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


if __name__ == "__main__":
    raise SystemExit(main())
