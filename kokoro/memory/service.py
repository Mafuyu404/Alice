"""Facade that wires event log, store, consolidator, recall, and vector index."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from kokoro.memory.consolidator import MemoryConsolidator
from kokoro.memory.event_log import MemoryEventLog
from kokoro.memory.experience import MemoryExperienceWorker
from kokoro.memory.index import MemoryIndex
from kokoro.memory.lifecycle import MemoryLifecycleWorker
from kokoro.memory.models import MemoryEventDraft, MemoryRecordDraft
from kokoro.memory.recall import MemoryRecall
from kokoro.memory.store import MemoryStore
from kokoro.memory.workspace import MemoryWorkspace
from kokoro.memory.working_context import MemoryWorkingContext


_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


class MemorySystem:
    def __init__(
        self,
        *,
        character_id: str,
        root: Path | None = None,
        vector_backend: object | None = None,
        llm_call=None,
    ) -> None:
        self.character_id = character_id
        self.root = Path(root or _ROOT)
        self.vector_backend = vector_backend
        self.event_log = MemoryEventLog(character_id=character_id, root=self.root)
        self.store = MemoryStore(character_id=character_id, root=self.root)
        self.workspace = MemoryWorkspace(character_id=character_id, root=self.root)
        self.working_context = MemoryWorkingContext(character_id=character_id, root=self.root)
        self.index = MemoryIndex(character_id=character_id, vector_backend=vector_backend)
        self.consolidator = MemoryConsolidator(character_id=character_id, llm_call=llm_call)
        self.experience = MemoryExperienceWorker(memory_system=self, llm_call=llm_call)
        self.lifecycle = MemoryLifecycleWorker(memory_system=self, llm_call=llm_call)
        self.auto_wake_lifecycle = True
        self.inline_maintenance_enabled = True
        self.recall = MemoryRecall(
            character_id=character_id,
            store=self.store,
            index=self.index,
            working_context=self.working_context,
        )
        self._lifecycle_stop = threading.Event()
        self._lifecycle_wake = threading.Event()
        self._lifecycle_thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self.last_experience_result: dict[str, object] = {}

    @property
    def ready(self) -> bool:
        return True

    def append_event(self, event: MemoryEventDraft) -> str:
        event_id = self.event_log.append(event)
        if self.auto_wake_lifecycle:
            self.wake_lifecycle_worker()
        return event_id

    def append_input_event(self, event: object) -> str:
        event_id = self.event_log.append_input_event(event)
        self.wake_lifecycle_worker()
        return event_id

    def remember(
        self,
        content: str,
        *,
        inner_stream: str = "",
        recent_context: str = "",
        event_batch: str = "",
        importance: str | float = "medium",
        source_event_ids: list[str] | None = None,
    ):
        draft = self.consolidator.prepare_write(
            content=content,
            inner_stream=inner_stream,
            recent_context=recent_context,
            event_batch=event_batch,
            importance=importance,
            source_event_ids=source_event_ids or [],
        )
        if draft is None:
            return None
        record, merged = self.store.write_or_merge(draft)
        self.index.sync_record(content=record.content, summary=record.summary, tags=record.tags)
        self.working_context.append_recent_memory(record.summary or record.content, source="merged" if merged else "written")
        return record

    def write_draft(self, draft: MemoryRecordDraft):
        record, merged = self.store.write_or_merge(draft)
        self.index.sync_record(content=record.content, summary=record.summary, tags=record.tags)
        self.working_context.append_recent_memory(record.summary or record.content, source="merged" if merged else "written")
        return record

    def default_context(self, **kwargs) -> str:
        if "recent_memory_digest" not in kwargs:
            kwargs["recent_memory_digest"] = self.working_context.read_recent_memory_digest()
        recall_context = self.recall.default_context(**kwargs)
        workspace_context = self.workspace.as_context(max_chars=2400)
        if not workspace_context:
            return recall_context
        return f"{recall_context}\n\n【经验工作区：记忆材料，不是当前现场】\n{workspace_context}".strip()

    def deep_recall(self, query: str, *, limit: int = 8) -> str:
        return self.recall.deep_recall(query, limit=limit)

    def recent_events_text(self, *, limit: int = 20, max_chars: int = 3000) -> str:
        events = self.event_log.recent_events(limit=limit)
        lines = []
        for event in events:
            content = str(event.get("content") or "").strip()
            if content:
                lines.append(f"- [{event.get('timestamp', '')}] {event.get('source', '')}: {content}")
        return "\n".join(lines)[-max_chars:]

    def recent_memory_digest(self, *, max_chars: int = 2400) -> str:
        return self.working_context.read_recent_memory_digest(max_chars=max_chars)

    def sediment_once(self):
        self.experience.run_once()
        return self.lifecycle.run_once()

    def maintenance_once(self, *, max_batches: int = 1):
        decisions = []
        batches = max(1, int(max_batches or 1))
        with self._lifecycle_lock:
            for _ in range(batches):
                experience_result = self.experience.run_once()
                self.last_experience_result = {
                    "updated": bool(getattr(experience_result, "updated", False)),
                    "event_count": int(getattr(experience_result, "event_count", 0) or 0),
                    "notes": str(getattr(experience_result, "notes", "") or ""),
                }
                decision = self.lifecycle.run_once()
                decisions.append(decision)
                if not decision.remember and not decision.archive:
                    break
        return decisions

    def set_lifecycle_llm(self, llm_call) -> None:
        self.experience.llm_call = llm_call
        self.lifecycle.llm_call = llm_call
        if getattr(self.consolidator, "llm_call", None) is None:
            self.consolidator.llm_call = llm_call

    def set_lifecycle_runtime_mode(self, *, auto_wake: bool | None = None, inline: bool | None = None) -> None:
        if auto_wake is not None:
            self.auto_wake_lifecycle = bool(auto_wake)
        if inline is not None:
            self.inline_maintenance_enabled = bool(inline)

    def start_lifecycle_worker(
        self,
        *,
        interval_seconds: float = 30.0,
        max_batches_per_wake: int = 3,
    ) -> None:
        if self._lifecycle_thread and self._lifecycle_thread.is_alive():
            return
        self._lifecycle_stop.clear()
        self._lifecycle_wake.clear()
        interval = max(1.0, float(interval_seconds or 30.0))
        max_batches = max(1, int(max_batches_per_wake or 1))
        self._lifecycle_thread = threading.Thread(
            target=self._run_lifecycle_worker,
            kwargs={"interval_seconds": interval, "max_batches_per_wake": max_batches},
            daemon=True,
            name=f"memory-lifecycle-{self.character_id}",
        )
        self._lifecycle_thread.start()

    def stop_lifecycle_worker(self, *, wait: bool = True, timeout: float = 3.0) -> None:
        self._lifecycle_stop.set()
        self._lifecycle_wake.set()
        thread = self._lifecycle_thread
        if wait and thread and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))

    def wake_lifecycle_worker(self) -> None:
        self._lifecycle_wake.set()

    def _run_lifecycle_worker(self, *, interval_seconds: float, max_batches_per_wake: int) -> None:
        while not self._lifecycle_stop.is_set():
            self._lifecycle_wake.wait(timeout=interval_seconds)
            self._lifecycle_wake.clear()
            if self._lifecycle_stop.is_set():
                break
            for _ in range(max_batches_per_wake):
                if self._lifecycle_stop.is_set():
                    break
                with self._lifecycle_lock:
                    try:
                        experience_result = self.experience.run_once()
                        self.last_experience_result = {
                            "updated": bool(getattr(experience_result, "updated", False)),
                            "event_count": int(getattr(experience_result, "event_count", 0) or 0),
                            "notes": str(getattr(experience_result, "notes", "") or ""),
                        }
                        decision = self.lifecycle.run_once()
                    except Exception as exc:
                        logger.warning("memory lifecycle worker failed: %s", exc)
                        break
                if not decision.remember and not decision.archive:
                    break
                time.sleep(0.05)
