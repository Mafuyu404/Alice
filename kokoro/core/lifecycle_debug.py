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


def enabled() -> bool:
    return bool(path())


def path() -> str:
    return str(os.environ.get("KOKORO_LIFECYCLE_TRACE") or "").strip()


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
    except Exception:
        # Debug tracing must never change runtime behavior.
        return


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
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
        return _safe(vars(value))
    return repr(value)
