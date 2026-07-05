"""Data objects for the Alice memory layer."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


MemoryPolicy = Literal["experience", "control", "debug", "ephemeral", "blocked"]
RecordForm = Literal["raw_event", "episode_note", "distilled_note", "open_thread", "association_note"]
LinkType = Literal["temporal_near", "same_event", "same_open_thread", "text_near", "recall_together"]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass
class MemoryEventDraft:
    character_id: str
    content: str
    source: str = "unknown"
    event_type: str = "text"
    memory_policy: MemoryPolicy = "experience"
    timestamp: str = field(default_factory=now_iso)
    event_id: str = field(default_factory=lambda: new_id("evt"))
    participants: list[str] = field(default_factory=list)
    tool_name: str = ""
    links: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "character_id": self.character_id,
            "timestamp": self.timestamp,
            "source": self.source,
            "type": self.event_type,
            "content": self.content,
            "participants": list(self.participants),
            "tool_name": self.tool_name,
            "memory_policy": self.memory_policy,
            "links": dict(self.links),
            "metadata": dict(self.metadata),
        }


@dataclass
class MemoryRecordDraft:
    character_id: str
    content: str
    record_form: RecordForm = "episode_note"
    summary: str = ""
    importance: float = 0.5
    emotional_impact: float = 0.0
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_event_ids: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    related_memory_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryRecord:
    id: str
    character_id: str
    record_form: str
    content: str
    summary: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_accessed_at: str = ""
    last_diffused_at: str = ""
    access_count: float = 0.0
    direct_access_count: float = 0.0
    diffused_access_count: float = 0.0
    importance: float = 0.5
    emotional_impact: float = 0.0
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_event_ids: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    related_memory_ids: list[str] = field(default_factory=list)
    index_status: str = "pending"
    deleted_at: str = ""
    score: float = 0.0

    @classmethod
    def from_row(cls, row: Any) -> "MemoryRecord":
        def loads(value: str, default):
            if not value:
                return default
            try:
                return json.loads(value)
            except Exception:
                return default

        return cls(
            id=row["id"],
            character_id=row["character_id"],
            record_form=row["record_form"],
            content=row["content"],
            summary=row["summary"] or "",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
            last_accessed_at=row["last_accessed_at"] or "",
            last_diffused_at=row["last_diffused_at"] or "",
            access_count=float(row["access_count"] or 0.0),
            direct_access_count=float(row["direct_access_count"] or 0.0),
            diffused_access_count=float(row["diffused_access_count"] or 0.0),
            importance=float(row["importance"] or 0.0),
            emotional_impact=float(row["emotional_impact"] or 0.0),
            keywords=loads(row["keywords_json"], []),
            tags=loads(row["tags_json"], []),
            source_event_ids=loads(row["source_event_ids_json"], []),
            evidence=loads(row["evidence_json"], []),
            related_memory_ids=loads(row["related_memory_ids_json"], []),
            index_status=row["index_status"] or "pending",
            deleted_at=row["deleted_at"] or "",
        )


def clamp01(value: float, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)
