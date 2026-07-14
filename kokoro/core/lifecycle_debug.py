"""Verbose lifecycle trace for inner-stream driven runtime debugging."""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_RUN_DIR_ENV = "KOKORO_DEBUG_RUN_DIR"
_TRACE_ENV = "KOKORO_LIFECYCLE_TRACE"


def enabled() -> bool:
    return bool(path())


def path() -> str:
    return str(os.environ.get(_TRACE_ENV) or "").strip()


def run_dir() -> str:
    return str(os.environ.get(_RUN_DIR_ENV) or "").strip()


def configure_run(run_dir_path: str | os.PathLike[str], *, metadata: dict[str, Any] | None = None) -> Path:
    """Enable verbose split debug output for one runtime invocation."""

    target = Path(run_dir_path)
    target.mkdir(parents=True, exist_ok=True)
    os.environ[_RUN_DIR_ENV] = str(target)
    os.environ[_TRACE_ENV] = str(target / "lifecycle_trace.jsonl")
    meta = {
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        **(metadata or {}),
    }
    try:
        (target / "run.json").write_text(json.dumps(_safe(meta), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return target


def log(event_name: str, **fields: Any) -> None:
    trace_path = path()
    if not trace_path:
        return
    if "event" in fields:
        fields["subject_event"] = fields.pop("event")
    record = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(),
        "monotonic": round(time.monotonic(), 6),
        "event": str(event_name or "unknown"),
        **fields,
    }
    try:
        target = Path(trace_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(_safe(record), ensure_ascii=False, sort_keys=True)
        with _LOCK:
            with target.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
            _write_split_logs(_safe(record))
    except Exception:
        # Debug tracing must never change runtime behavior.
        return


def _write_split_logs(record: dict[str, Any]) -> None:
    root_text = run_dir()
    if not root_text:
        return
    root = Path(root_text)
    routes = _routes_for(record)
    for rel in routes:
        _append_jsonl(root / rel, record)


def _append_jsonl(target: Path, record: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _routes_for(record: dict[str, Any]) -> list[Path]:
    event = str(record.get("event") or "")
    lower = event.lower()
    routes: list[Path] = [Path("all") / "events.jsonl"]

    if event.startswith("life.runtime") or event.startswith("life.local_thinking"):
        routes.append(Path("thinking") / "events.jsonl")
    if "thought" in lower or "local_thinking" in lower:
        routes.append(Path("thinking") / "thoughts.jsonl")
    if "prompt" in lower or "tool_select" in lower:
        routes.append(Path("thinking") / "prompts.jsonl")
    if event.startswith("life.context") or "context" in lower or "compact" in lower:
        routes.append(Path("context") / "events.jsonl")
    if "memory" in lower or _contains_key(record, "memory_policy"):
        routes.append(Path("memory") / "events.jsonl")
    if event.startswith("tool_registry") or event.startswith("action_runtime") or "action_plan" in lower or "tool_" in lower:
        routes.append(Path("tools") / "events.jsonl")
    if event.startswith("life.event_pool") or event.startswith("chat_session.record_") or event.startswith("debug_input"):
        routes.append(Path("inputs") / "events.jsonl")
    if "error" in lower or "failed" in lower or str(record.get("status") or "").lower() == "failed":
        routes.append(Path("errors") / "events.jsonl")

    tool = _tool_name(record)
    if tool:
        safe_tool = _safe_filename(tool)
        routes.append(Path("tools") / safe_tool / "events.jsonl")
    return _dedupe_paths(routes)


def _tool_name(record: dict[str, Any]) -> str:
    for key in ("tool", "action"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    found = _find_nested_tool_name(record)
    if found:
        return found
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        action = metadata.get("action")
        if isinstance(action, str) and action.strip():
            return action.strip()
    return ""


def _find_nested_tool_name(value: Any, *, depth: int = 0) -> str:
    if depth > 8:
        return ""
    if isinstance(value, dict):
        for key in ("tool", "action"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, (dict, list)):
                found = _find_nested_tool_name(item, depth=depth + 1)
                if found:
                    return found
        for key in ("prepared", "metadata", "result"):
            item = value.get(key)
            if isinstance(item, (dict, list)):
                found = _find_nested_tool_name(item, depth=depth + 1)
                if found:
                    return found
        for item in value.values():
            if isinstance(item, (dict, list)):
                found = _find_nested_tool_name(item, depth=depth + 1)
                if found:
                    return found
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                found = _find_nested_tool_name(item, depth=depth + 1)
                if found:
                    return found
    return ""


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        if key in value:
            return True
        return any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def _safe_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value).strip())
    return safe[:80] or "unknown"


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path_value in paths:
        key = str(path_value)
        if key in seen:
            continue
        seen.add(key)
        result.append(path_value)
    return result


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if callable(value):
        return {
            "type": "callable",
            "name": getattr(value, "__name__", type(value).__name__),
        }
    if dataclasses.is_dataclass(value):
        return _safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(v) for v in value]
    if hasattr(value, "visible_content") and hasattr(value, "metadata"):
        return {
            "id": getattr(value, "id", ""),
            "type": getattr(value, "type", ""),
            "source": getattr(value, "source", ""),
            "content": getattr(value, "content", ""),
            "visible_content": value.visible_content(),
            "timestamp": getattr(value, "timestamp", ""),
            "metadata": _safe(getattr(value, "metadata", {})),
            "priority": getattr(value, "priority", ""),
            "lifetime": getattr(value, "lifetime", ""),
            "privacy": _safe(getattr(value, "privacy", None)),
        }
    if hasattr(value, "__dict__"):
        module = str(getattr(type(value), "__module__", "") or "")
        if module and not module.startswith("kokoro"):
            return {"type": type(value).__name__, "repr": repr(value)[:200]}
        return _safe(vars(value))
    return repr(value)
