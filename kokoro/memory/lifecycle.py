"""Full event sedimentation and model-driven memory lifecycle."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from kokoro.memory.models import MemoryRecordDraft, now_iso
from kokoro.prompt.templates import load_template


LlmCall = Callable[[list[dict], dict], str]


@dataclass
class MemoryLifecycleDecision:
    remember: list[MemoryRecordDraft] = field(default_factory=list)
    archive: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""


class MemoryLifecycleWorker:
    """Turns append-only experience events into retained memory or cold archive.

    The worker is intentionally model-driven: hard code only handles IO,
    parsing, cursor movement, and safety bounds. Meaning, retention, and
    forgetting decisions come from the LLM prompt.
    """

    def __init__(
        self,
        *,
        memory_system: object,
        llm_call: LlmCall | None = None,
        batch_size: int = 80,
        max_chars: int = 12000,
    ) -> None:
        self.memory_system = memory_system
        self.character_id = str(getattr(memory_system, "character_id", "") or "")
        self.root = Path(getattr(memory_system, "root", Path.cwd()))
        self.llm_call = llm_call
        self.batch_size = max(1, int(batch_size))
        self.max_chars = max(1000, int(max_chars))
        self.path = self.root / "characters" / self.character_id / "memory"
        self.path.mkdir(parents=True, exist_ok=True)
        self.cursor_path = self.path / "lifecycle_cursor.json"
        self.archive_path = self.path / "archive" / "forgotten.jsonl"
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)

    def run_once(self) -> MemoryLifecycleDecision:
        events = self._next_events()
        if not events:
            return MemoryLifecycleDecision(notes="no new events")
        decision = self._decide(events)
        for draft in decision.remember:
            write_draft = getattr(self.memory_system, "write_draft", None)
            if callable(write_draft):
                write_draft(draft)
        self._append_archive(decision.archive)
        self._advance_cursor(events)
        return decision

    def _next_events(self) -> list[dict[str, Any]]:
        event_log = getattr(self.memory_system, "event_log", None)
        event_path = getattr(event_log, "path", self.path / "events")
        event_path = Path(event_path)
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

    def _decide(self, events: list[dict[str, Any]]) -> MemoryLifecycleDecision:
        if self.llm_call is None:
            return MemoryLifecycleDecision(
                archive=[_archive_item(event, reason="no lifecycle llm configured") for event in events],
                notes="archived without model decision",
            )
        system = load_template("memory/life/lifecycle_system.md")
        user_template = load_template("memory/life/lifecycle_user.md")
        if not system or not user_template:
            return MemoryLifecycleDecision(
                archive=[_archive_item(event, reason="missing lifecycle prompt") for event in events],
                notes="archived without prompts",
            )
        user = user_template.replace(
            "{{ events_json }}",
            json.dumps([_event_for_prompt(event) for event in events], ensure_ascii=False, indent=2),
        )
        try:
            raw = self.llm_call(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                {"function": "memory_lifecycle", "max_tokens": 1200},
            )
            data = _extract_json(raw)
        except Exception:
            data = None
        if not isinstance(data, dict):
            return MemoryLifecycleDecision(
                archive=[_archive_item(event, reason="lifecycle decision parse failed") for event in events],
                notes="parse failed",
            )
        return self._decision_from_json(data, events)

    def _decision_from_json(
        self,
        data: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> MemoryLifecycleDecision:
        drafts: list[MemoryRecordDraft] = []
        event_ids = {str(event.get("event_id") or "") for event in events}
        for item in data.get("remember", []) if isinstance(data.get("remember"), list) else []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            source_ids = [str(x) for x in item.get("source_event_ids", []) if str(x) in event_ids]
            record_form = str(item.get("record_form") or "episode_note").strip()
            if record_form not in {"raw_event", "episode_note", "distilled_note", "open_thread", "association_note"}:
                record_form = "episode_note"
            drafts.append(
                MemoryRecordDraft(
                    character_id=self.character_id,
                    record_form=record_form,  # type: ignore[arg-type]
                    content=content,
                    summary=str(item.get("summary") or "").strip(),
                    importance=_importance_value(item.get("importance", "medium")),
                    emotional_impact=_float(item.get("emotional_impact"), 0.0),
                    keywords=_as_list(item.get("keywords")),
                    tags=_as_list(item.get("tags")),
                    source_event_ids=source_ids,
                    evidence=item.get("evidence") if isinstance(item.get("evidence"), list) else [],
                    metadata={"lifecycle": "sedimented"},
                )
            )
        archive = []
        archived_ids: set[str] = set()
        for item in data.get("archive", []) if isinstance(data.get("archive"), list) else []:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("event_id") or "").strip()
            if event_id and event_id in event_ids:
                archived_ids.add(event_id)
                archive.append(
                    {
                        "event_id": event_id,
                        "reason": str(item.get("reason") or "").strip(),
                        "archived_at": now_iso(),
                    }
                )
        remembered_ids = {source_id for draft in drafts for source_id in draft.source_event_ids}
        for event in events:
            event_id = str(event.get("event_id") or "").strip()
            if event_id and event_id not in remembered_ids and event_id not in archived_ids:
                archive.append(
                    {
                        "event_id": event_id,
                        "reason": "not selected by lifecycle decision",
                        "archived_at": now_iso(),
                    }
                )
        return MemoryLifecycleDecision(
            remember=drafts,
            archive=archive,
            notes=str(data.get("notes") or "").strip(),
        )

    def _append_archive(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        with self.archive_path.open("a", encoding="utf-8") as file:
            for item in items:
                file.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")

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
    return {
        "event_id": event.get("event_id"),
        "timestamp": event.get("timestamp"),
        "source": event.get("source"),
        "type": event.get("type"),
        "tool_name": event.get("tool_name"),
        "memory_policy": event.get("memory_policy"),
        "content": event.get("content"),
    }


def _archive_item(event: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "reason": reason,
        "archived_at": now_iso(),
    }


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


def _importance_value(value) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    text = str(value or "").strip().lower()
    return {"high": 0.85, "medium": 0.55, "low": 0.25}.get(text, 0.55)


def _float(value, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _as_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result[:24]
