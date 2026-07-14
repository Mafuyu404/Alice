"""Memory recall with lightweight diffusion and prompt-safe formatting."""

from __future__ import annotations

import json
import re
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
        parts = [
            part
            for part in (event_text, inner_stream, recent_digest, recent_memory_digest, pending_threads)
            if str(part or "").strip()
        ]
        query = "\n".join(parts)
        if not query.strip():
            query = inner_stream or recent_digest or recent_memory_digest or pending_threads
        if not query.strip():
            return ""

        result = self.recall(query, limit=limit, include_vector=False, record_usage=False, use_access=False)
        event_query = _event_recall_query(event_text)
        if event_query:
            event_result = self.recall(event_query, limit=limit, include_vector=False, record_usage=False, use_access=False)
            result = _merge_recall_results(event_result, result, focus_limit=min(3, max(1, int(limit))), side_limit=4)
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
        use_access: bool = True,
    ) -> RecallResult:
        query = str(query or "").strip()
        if not query:
            return RecallResult()
        hits = self.store.search(query, limit=limit, use_access=use_access)
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


def _merge_recall_results(primary: RecallResult, secondary: RecallResult, *, focus_limit: int, side_limit: int) -> RecallResult:
    focus: list[MemoryRecord] = []
    side: list[MemoryRecord] = []
    faint: list[str] = []
    seen: set[str] = set()

    def add_focus(record: MemoryRecord) -> None:
        if record.id in seen:
            return
        if len(focus) < focus_limit:
            focus.append(record)
        elif len(side) < side_limit:
            side.append(record)
        else:
            faint.append(_short_hint(record))
        seen.add(record.id)

    def add_side(record: MemoryRecord) -> None:
        if record.id in seen:
            return
        if len(side) < side_limit:
            side.append(record)
        else:
            faint.append(_short_hint(record))
        seen.add(record.id)

    for record in primary.focus:
        add_focus(record)
    for record in secondary.focus:
        add_focus(record)
    for record in primary.side:
        add_side(record)
    for record in secondary.side:
        add_side(record)
    for hint in primary.faint + secondary.faint:
        if hint and hint not in faint:
            faint.append(hint)

    vector_text = primary.vector_text or secondary.vector_text
    return RecallResult(focus=focus, side=side[:side_limit], faint=faint[:5], vector_text=vector_text)


def _event_recall_query(event_text: str) -> str:
    text = str(event_text or "").strip()
    if not text:
        return ""
    marker = "最近消息："
    if marker in text:
        tail = text.rsplit(marker, 1)[-1].strip()
        return tail or text
    return text


def _record_line(record: MemoryRecord, *, compact: bool = False) -> str:
    text = _record_prompt_text(record)
    if len(text) > (180 if compact else 320):
        text = text[: 180 if compact else 320].rstrip() + "..."
    tags = " ".join(f"#{tag}" for tag in record.tags[:3])
    when = record.created_at[:16].replace("T", " ") if record.created_at else ""
    prefix = f"{when} " if when else ""
    suffix = f" {tags}" if tags else ""
    return f"{prefix}{text}{suffix}".strip()


def _short_hint(record: MemoryRecord) -> str:
    text = _record_prompt_text(record)
    return text[:90].rstrip() + ("..." if len(text) > 90 else "")


def _record_prompt_text(record: MemoryRecord) -> str:
    summary = _clean_prompt_text(str(record.summary or "").strip())
    content = _clean_prompt_text(record.content)
    if summary and content and summary not in content:
        return f"{summary}：{content}"
    return summary or content


def _clean_prompt_text(text: str) -> str:
    text = _strip_fence(str(text or "").strip())
    if not text:
        return ""
    data = _try_json(text)
    if isinstance(data, dict):
        extracted = _text_from_json_memory(data)
        if extracted:
            return extracted
    text = _normalize_legacy_tool_receipt(text)
    text = _remove_recall_boilerplate(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _strip_fence(text: str) -> str:
    match = re.fullmatch(r"\s*```(?:json|txt|text|plaintext)?\s*(.*?)\s*```\s*", text, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _try_json(text: str) -> object:
    try:
        return json.loads(text)
    except Exception:
        return None


def _text_from_json_memory(data: dict) -> str:
    parts: list[str] = []
    for key in ("summary", "content", "memory", "memory_note", "current_experience", "notes"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, list):
            parts.extend(str(item).strip() for item in value if str(item).strip())
    patch = data.get("inner_stream_patch")
    if isinstance(patch, dict):
        for item in patch.get("patches", []) if isinstance(patch.get("patches"), list) else []:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                parts.append(str(item["text"]).strip())
        reason = str(patch.get("reason") or "").strip()
        if reason:
            parts.append(reason)
    remember = data.get("remember")
    if isinstance(remember, list):
        for item in remember:
            if isinstance(item, dict):
                for key in ("summary", "content"):
                    value = str(item.get(key) or "").strip()
                    if value:
                        parts.append(value)
                        break
    cleaned = []
    for part in parts:
        part = _remove_recall_boilerplate(_normalize_legacy_tool_receipt(_strip_fence(part)))
        if part:
            cleaned.append(part)
    return " / ".join(dict.fromkeys(cleaned)).strip()


def _normalize_legacy_tool_receipt(text: str) -> str:
    text = re.sub(r"我刚刚搜索了[：:]\s*", "搜索线索：", text)
    text = re.sub(r"搜索结果[：:]\s*", "结果片段：", text)
    text = text.replace("[web_search_result]", "web 搜索材料")
    text = re.sub(r"action_id\s*=\s*\S+", "", text)
    return text.strip()


def _remove_recall_boilerplate(text: str) -> str:
    skip_prefixes = (
        "刚被带出的记忆材料",
        "连带出现的记忆材料",
        "更远一些的记忆线索",
        "这些只是被呈现出来的材料",
        "是否接住、放下",
        "【当前经验工作区】",
        "【经验工作区",
        "## current_experience",
        "## open_threads",
        "## recent_raw_digest",
        "## workspace_notes",
    )
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(stripped.startswith(prefix) for prefix in skip_prefixes):
            continue
        lines.append(stripped)
    return "\n".join(lines).strip()


def _trim_vector_text(text: str, max_chars: int = 900) -> str:
    text = str(text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n..."
