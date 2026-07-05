"""Model-driven memory lifecycle from experience workspace to long memory."""

from __future__ import annotations

import hashlib
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
    """Turns the model-maintained workspace into retained memory."""

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
        workspace_text = self._workspace_text()
        workspace_hash = _hash_text(workspace_text)
        cursor = self._read_cursor()
        if not workspace_text.strip():
            return MemoryLifecycleDecision(notes="empty workspace")
        if workspace_hash and workspace_hash == cursor.get("workspace_hash"):
            return MemoryLifecycleDecision(notes="workspace unchanged")
        decision = self._decide(workspace_text, workspace_hash)
        for draft in decision.remember:
            write_draft = getattr(self.memory_system, "write_draft", None)
            if callable(write_draft):
                write_draft(draft)
        self._append_archive(decision.archive)
        self._advance_cursor(workspace_hash)
        return decision

    def _workspace_text(self) -> str:
        workspace = getattr(self.memory_system, "workspace", None)
        as_context = getattr(workspace, "as_context", None)
        if callable(as_context):
            return str(as_context(max_chars=self.max_chars) or "")
        return ""

    def _decide(self, workspace_text: str, workspace_hash: str) -> MemoryLifecycleDecision:
        if self.llm_call is None:
            return MemoryLifecycleDecision(notes="no lifecycle llm configured")
        system = load_template("memory/life/lifecycle_system.md")
        user_template = load_template("memory/life/lifecycle_user.md")
        if not system or not user_template:
            return MemoryLifecycleDecision(notes="missing lifecycle prompts")
        user = user_template.replace("{{ workspace_text }}", workspace_text)
        try:
            raw = self.llm_call(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                {"function": "memory_lifecycle", "max_tokens": 300, "timeout": 45},
            )
            data = _extract_json(raw)
        except Exception:
            data = None
        if not isinstance(data, dict):
            return MemoryLifecycleDecision(notes="parse failed")
        return self._decision_from_json(data, workspace_hash, workspace_text)

    def _decision_from_json(
        self,
        data: dict[str, Any],
        workspace_hash: str,
        workspace_text: str,
    ) -> MemoryLifecycleDecision:
        drafts: list[MemoryRecordDraft] = []
        for item in data.get("remember", []) if isinstance(data.get("remember"), list) else []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            source_ids = [str(x) for x in item.get("source_event_ids", []) if str(x).strip()]
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
                    metadata={"lifecycle": "sedimented", "workspace_hash": workspace_hash},
                )
            )
        archive: list[dict[str, Any]] = []
        for item in data.get("archive", []) if isinstance(data.get("archive"), list) else []:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("event_id") or "").strip()
            if event_id:
                archive.append(
                    {
                        "event_id": event_id,
                        "reason": str(item.get("reason") or "").strip(),
                        "archived_at": now_iso(),
                    }
                )
        if not drafts and not archive:
            for event_id in _event_ids_from_workspace(workspace_text):
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
            return {"workspace_hash": ""}
        return data if isinstance(data, dict) else {"workspace_hash": ""}

    def _advance_cursor(self, workspace_hash: str) -> None:
        if not workspace_hash:
            return
        self.cursor_path.write_text(
            json.dumps(
                {
                    "workspace_hash": workspace_hash,
                    "updated_at": now_iso(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def _hash_text(text: str) -> str:
    if not str(text or "").strip():
        return ""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _event_ids_from_workspace(text: str) -> list[str]:
    result: list[str] = []
    for match in re.finditer(r"\bevt_[A-Za-z0-9_\-]+", str(text or "")):
        event_id = match.group(0)
        if event_id not in result:
            result.append(event_id)
    return result[:100]


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
