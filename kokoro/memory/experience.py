"""Raw event digestion into the model-maintained experience workspace."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from kokoro.memory.models import now_iso
from kokoro.prompt.templates import load_template


LlmCall = Callable[[list[dict], dict], str]


@dataclass
class MemoryExperienceResult:
    updated: bool = False
    event_count: int = 0
    notes: str = ""


class MemoryExperienceWorker:
    """Turns raw experience events into a compact workspace.

    Code only moves bytes, cursors, and JSON. What the workspace means is left
    to the prompt so the memory layer stays aligned with the character rather
    than a fixed classifier.
    """

    def __init__(
        self,
        *,
        memory_system: object,
        llm_call: LlmCall | None = None,
        batch_size: int = 8,
        max_chars: int = 2500,
    ) -> None:
        self.memory_system = memory_system
        self.character_id = str(getattr(memory_system, "character_id", "") or "")
        self.root = Path(getattr(memory_system, "root", Path.cwd()))
        self.llm_call = llm_call
        self.batch_size = max(1, int(batch_size))
        self.max_chars = max(1000, int(max_chars))
        self.path = self.root / "characters" / self.character_id / "memory"
        self.path.mkdir(parents=True, exist_ok=True)
        self.cursor_path = self.path / "workspace" / "experience_cursor.json"
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)

    def run_once(self) -> MemoryExperienceResult:
        events = self._next_events()
        if not events:
            return MemoryExperienceResult(notes="no new raw events")
        updated = self._update_workspace(events)
        self._advance_cursor(events)
        return MemoryExperienceResult(updated=updated, event_count=len(events), notes="workspace updated" if updated else "workspace unchanged")

    def _next_events(self) -> list[dict[str, Any]]:
        event_log = getattr(self.memory_system, "event_log", None)
        event_path = Path(getattr(event_log, "path", self.path / "events"))
        cursor = self._read_cursor()
        result: list[dict[str, Any]] = []
        total_chars = 0
        for file_path in sorted(event_path.glob("*.jsonl")):
            if file_path.name < cursor.get("file", ""):
                continue
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            start_line = int(cursor.get("line", 0) or 0) if file_path.name == cursor.get("file") else 0
            for index, line in enumerate(lines[start_line:], start=start_line + 1):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = str(event.get("content") or "").strip()
                if not content:
                    continue
                event["_cursor_file"] = file_path.name
                event["_cursor_line"] = index
                result.append(event)
                total_chars += len(content)
                if len(result) >= self.batch_size or total_chars >= self.max_chars:
                    return result
        return result

    def _update_workspace(self, events: list[dict[str, Any]]) -> bool:
        workspace = getattr(self.memory_system, "workspace", None)
        if workspace is None:
            return False
        previous = workspace.read()
        if self.llm_call is None:
            digest = "\n".join(_event_line(event) for event in events)[-5000:]
            workspace.write(
                {
                    "current_experience": previous.current_experience,
                    "open_threads": previous.open_threads,
                    "recent_raw_digest": digest,
                    "notes": "experience workspace updated without llm",
                }
            )
            return True
        system = load_template("memory/life/experience_system.md")
        user_template = load_template("memory/life/experience_user.md")
        if not system or not user_template:
            return False
        user = (
            user_template.replace("{{ workspace_text }}", previous.as_context())
            .replace("{{ events_json }}", json.dumps([_event_for_prompt(event) for event in events], ensure_ascii=False, indent=2))
        )
        try:
            raw = self.llm_call(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                {"function": "memory_experience_workspace", "max_tokens": 300, "timeout": 45},
            )
            data = _extract_json(raw)
        except Exception:
            data = None
        if not isinstance(data, dict):
            return False
        if not any(key in data for key in ("current_experience", "open_threads", "recent_raw_digest")):
            digest = "\n".join(_event_line(event) for event in events)[-5000:]
            workspace.write(
                {
                    "current_experience": previous.current_experience,
                    "open_threads": previous.open_threads,
                    "recent_raw_digest": digest,
                    "notes": "experience workspace used raw digest because model returned non-workspace JSON",
                }
            )
            return True
        workspace.write(data)
        return True

    def _read_cursor(self) -> dict[str, Any]:
        try:
            data = json.loads(self.cursor_path.read_text(encoding="utf-8"))
        except Exception:
            return {"file": "", "line": 0}
        return data if isinstance(data, dict) else {"file": "", "line": 0}

    def _advance_cursor(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        last = events[-1]
        self.cursor_path.write_text(
            json.dumps(
                {
                    "file": str(last.get("_cursor_file") or ""),
                    "line": int(last.get("_cursor_line") or 0),
                    "updated_at": now_iso(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def _event_for_prompt(event: dict[str, Any]) -> dict[str, Any]:
    content = str(event.get("content") or "")
    if len(content) > 600:
        content = content[:600].rstrip() + "\n..."
    return {
        "event_id": event.get("event_id"),
        "timestamp": event.get("timestamp"),
        "source": event.get("source"),
        "type": event.get("type"),
        "tool_name": event.get("tool_name"),
        "memory_policy": event.get("memory_policy"),
        "content": content,
    }


def _event_line(event: dict[str, Any]) -> str:
    return f"- {event.get('event_id', '')} [{event.get('timestamp', '')}] {event.get('source', '')}/{event.get('type', '')}: {event.get('content', '')}"


def _extract_json(text: str):
    raw = re.sub(r"```(?:json)?\s*\n?(.*?)```", r"\1", str(text or ""), flags=re.DOTALL).strip()
    try:
        return json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
