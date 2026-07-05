"""Alice-owned memory layer.

This package is the memory system described in ``doc/memory_life_architecture.md``.
It keeps role memory as Alice data first, while optional vector backends such as
mem0 remain indexing helpers.
"""

from __future__ import annotations

from pathlib import Path

from kokoro.memory.consolidator import MemoryConsolidator
from kokoro.memory.event_log import MemoryEventLog
from kokoro.memory.index import MemoryIndex
from kokoro.memory.lifecycle import MemoryLifecycleDecision, MemoryLifecycleWorker
from kokoro.memory.models import MemoryEventDraft, MemoryRecord, MemoryRecordDraft
from kokoro.memory.recall import MemoryRecall
from kokoro.memory.service import MemorySystem
from kokoro.memory.store import MemoryStore
from kokoro.memory.working_context import MemoryWorkingContext


def create_memory_system(
    *,
    character_id: str,
    root: Path | str | None = None,
    vector_backend: object | None = None,
    llm_call=None,
) -> MemorySystem:
    return MemorySystem(
        character_id=character_id,
        root=Path(root) if root is not None else None,
        vector_backend=vector_backend,
        llm_call=llm_call,
    )


__all__ = [
    "MemoryConsolidator",
    "MemoryEventDraft",
    "MemoryEventLog",
    "MemoryIndex",
    "MemoryLifecycleDecision",
    "MemoryLifecycleWorker",
    "MemoryRecall",
    "MemoryRecord",
    "MemoryRecordDraft",
    "MemoryStore",
    "MemorySystem",
    "MemoryWorkingContext",
    "create_memory_system",
]
