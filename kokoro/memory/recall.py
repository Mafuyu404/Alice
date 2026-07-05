"""Memory recall with lightweight diffusion and prompt-safe formatting."""

from __future__ import annotations

from dataclasses import dataclass, field

from kokoro.memory.index import MemoryIndex
from kokoro.memory.models import MemoryRecord
from kokoro.memory.store import MemoryStore
from kokoro.memory.working_context import MemoryWorkingContext


@dataclass
class RecallResult:
    focus: list[MemoryRecord] = field(default_factory=list)
    side: list[MemoryRecord] = field(default_factory=list)
    faint: list[str] = field(default_factory=list)
    vector_text: str = ""

    def record_ids(self) -> list[str]:
        ids = [record.id for record in self.focus + self.side]
        return list(dict.fromkeys(ids))


class MemoryRecall:
    def __init__(
        self,
        *,
        character_id: str,
        store: MemoryStore,
        index: MemoryIndex | None = None,
        working_context: MemoryWorkingContext | None = None,
    ) -> None:
        self.character_id = character_id
        self.store = store
        self.index = index
        self.working_context = working_context

    def default_context(
        self,
        *,
        event_text: str = "",
        inner_stream: str = "",
        recent_digest: str = "",
        recent_memory_digest: str = "",
        pending_threads: str = "",
        limit: int = 6,
    ) -> str:
        query = "\n".join(
            part
            for part in (event_text, inner_stream, recent_digest, recent_memory_digest, pending_threads)
            if str(part or "").strip()
        )
        if not query.strip():
            query = inner_stream or recent_digest or recent_memory_digest or pending_threads
        result = self.recall(query, limit=limit, include_vector=False, record_usage=False)
        return self.format_for_prompt(result)

    def deep_recall(self, query: str, *, limit: int = 8) -> str:
        result = self.recall(query, limit=limit, include_vector=True, record_usage=True)
        return self.format_for_prompt(result, deep=True)

    def recall(
        self,
        query: str,
        *,
        limit: int = 8,
        include_vector: bool = True,
        record_usage: bool = True,
    ) -> RecallResult:
        query = str(query or "").strip()
        if not query:
            return RecallResult()
        hits = self.store.search(query, limit=limit)
        focus = hits[: max(1, min(3, len(hits)))]
        side: list[MemoryRecord] = []
        faint: list[str] = []
        for record in focus:
            known = {item.id for item in focus + side}
            for neighbor in self.store.neighbors(record.id, limit=3):
                if neighbor.id in known:
                    continue
                if neighbor.score >= 0.3:
                    side.append(neighbor)
                else:
                    faint.append(_short_hint(neighbor))
                known.add(neighbor.id)
        for record in hits[len(focus) :]:
            if len(side) < 4:
                side.append(record)
            else:
                faint.append(_short_hint(record))
        vector_text = self._vector_context(query) if include_vector else ""
        result = RecallResult(focus=focus, side=side[:4], faint=faint[:5], vector_text=vector_text)
        if record_usage:
            self.store.record_access(result.record_ids(), diffuse=True)
            self._append_recent_recall(result)
        return result

    def format_for_prompt(self, result: RecallResult, *, deep: bool = False) -> str:
        parts: list[str] = []
        if result.focus:
            parts.append("刚被带出的记忆材料：")
            for record in result.focus:
                parts.append(f"- {_record_line(record)}")
        if result.side:
            parts.append("\n连带出现的记忆材料：")
            for record in result.side:
                parts.append(f"- {_record_line(record, compact=True)}")
        if result.faint:
            parts.append("\n更远一些的记忆线索：")
            for hint in result.faint:
                parts.append(f"- {hint}")
        if result.vector_text.strip():
            label = "向量索引额外带出的材料：" if deep else "向量索引提供的背景材料："
            parts.append(f"\n{label}\n{_trim_vector_text(result.vector_text)}")
        if not parts:
            return ""
        parts.append(
            "\n这些只是被呈现出来的材料，不代表它们一定重要，也不要求显式说“我想起了”。"
            "是否接住、放下、继续追问或写入 inner_stream，由你自己判断。"
        )
        return "\n".join(parts).strip()

    def _append_recent_recall(self, result: RecallResult) -> None:
        if self.working_context is None:
            return
        lines = []
        for record in result.focus[:3]:
            text = (record.summary or record.content).strip()
            if text:
                lines.append(text[:180])
        if lines:
            self.working_context.append_recent_memory(" / ".join(lines), source="recalled")

    def _vector_context(self, query: str) -> str:
        return self.index.context(query) if self.index is not None else ""


def _record_line(record: MemoryRecord, *, compact: bool = False) -> str:
    text = (record.summary or record.content).strip() if compact else record.content.strip()
    if len(text) > (180 if compact else 320):
        text = text[: 180 if compact else 320].rstrip() + "..."
    tags = " ".join(f"#{tag}" for tag in record.tags[:3])
    when = record.created_at[:16].replace("T", " ") if record.created_at else ""
    prefix = f"{when} " if when else ""
    suffix = f" {tags}" if tags else ""
    return f"{prefix}{text}{suffix}".strip()


def _short_hint(record: MemoryRecord) -> str:
    text = (record.summary or record.content).strip()
    return text[:90].rstrip() + ("..." if len(text) > 90 else "")


def _trim_vector_text(text: str, max_chars: int = 900) -> str:
    text = str(text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n..."
