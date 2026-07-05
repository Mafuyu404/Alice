"""LLM-assisted preparation for memory writes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from kokoro.memory.models import MemoryRecordDraft
from kokoro.prompt.templates import load_template


LlmCall = Callable[[list[dict], dict], str]


class MemoryConsolidator:
    def __init__(self, *, character_id: str, llm_call: LlmCall | None = None) -> None:
        self.character_id = character_id
        self.llm_call = llm_call

    def prepare_write(
        self,
        *,
        content: str,
        inner_stream: str = "",
        recent_context: str = "",
        event_batch: str = "",
        importance: str | float = "medium",
        source_event_ids: list[str] | None = None,
    ) -> MemoryRecordDraft | None:
        content = str(content or "").strip()
        if not content:
            return None
        if self.llm_call is None:
            return self._fallback(content, importance=importance, source_event_ids=source_event_ids or [])

        system = load_template("memory/life/prepare_write_system.md")
        user_template = load_template("memory/life/prepare_write_user.md")
        if not system or not user_template:
            return self._fallback(content, importance=importance, source_event_ids=source_event_ids or [])
        user = (
            user_template.replace("{{ memory_note }}", content)
            .replace("{{ inner_stream }}", inner_stream or "(empty)")
            .replace("{{ recent_context }}", recent_context or "(none)")
            .replace("{{ event_batch }}", event_batch or "(none)")
        )
        try:
            raw = self.llm_call(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                {"function": "memory_prepare_write", "max_tokens": 512},
            )
            data = _extract_json(raw)
        except Exception:
            data = None
        if not isinstance(data, dict):
            return self._fallback(content, importance=importance, source_event_ids=source_event_ids or [])
        prepared = str(data.get("content") or content).strip()
        if not prepared:
            return None
        prepared = _preserve_source_details(source=content, prepared=prepared)
        record_form = str(data.get("record_form") or "episode_note").strip()
        if record_form not in {"raw_event", "episode_note", "distilled_note", "open_thread", "association_note"}:
            record_form = "episode_note"
        return MemoryRecordDraft(
            character_id=self.character_id,
            record_form=record_form,  # type: ignore[arg-type]
            content=prepared,
            summary=str(data.get("summary") or "").strip(),
            importance=_importance_value(data.get("importance", importance)),
            emotional_impact=_float(data.get("emotional_impact"), 0.0),
            keywords=_as_list(data.get("keywords")),
            tags=_as_list(data.get("tags")),
            source_event_ids=_as_list(data.get("source_event_ids")) or list(source_event_ids or []),
            evidence=data.get("evidence") if isinstance(data.get("evidence"), list) else [],
        )

    def _fallback(self, content: str, *, importance: str | float, source_event_ids: list[str]) -> MemoryRecordDraft:
        return MemoryRecordDraft(
            character_id=self.character_id,
            record_form="episode_note",
            content=content.strip(),
            summary=content.strip()[:160],
            importance=_importance_value(importance),
            keywords=_keywords(content),
            source_event_ids=source_event_ids,
        )


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


def _keywords(text: str) -> list[str]:
    result = []
    for item in re.findall(r"[\w\u4e00-\u9fff]{2,}", str(text or "").lower()):
        if item not in result:
            result.append(item)
    return result[:12]


def _preserve_source_details(*, source: str, prepared: str) -> str:
    """Keep LLM consolidation from erasing concrete source details.

    The consolidator may polish language, but the original requested memory is
    the subject's intent. If the prepared text becomes too abstract, append the
    original material as a continuity anchor instead of replacing the model.
    """

    source = str(source or "").strip()
    prepared = str(prepared or "").strip()
    if not source or not prepared:
        return prepared or source
    source_terms = set(_keywords(source))
    prepared_terms = set(_keywords(prepared))
    missing = [term for term in source_terms if term not in prepared_terms]
    if len(source) > 24 and len(missing) >= max(2, len(source_terms) // 3):
        return f"{prepared}\n原始线索：{source}"
    return prepared
