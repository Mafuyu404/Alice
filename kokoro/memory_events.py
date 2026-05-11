"""Memory/date event detector for impulse planning context."""

from __future__ import annotations

import datetime as _dt
import re
import time
from dataclasses import dataclass
from typing import Any

from kokoro import prompts


@dataclass(frozen=True)
class MemoryEvent:
    score: float
    context: str
    source: str
    event_id: str


@dataclass
class MemoryEventConfig:
    enabled: bool = False
    check_interval: float = 300.0
    cooldown_seconds: float = 21600.0
    date_score: float = 50.0
    memory_score: float = 70.0
    query: str = "recent important user preferences, plans, dates, anniversaries, goals"
    date_events: list[dict[str, str]] | None = None


class MemoryEventDetector:
    def __init__(
        self,
        memory_backend: object,
        user_id: str,
        config: MemoryEventConfig,
    ) -> None:
        self.memory_backend = memory_backend
        self.user_id = user_id
        self.config = config
        self._last_emit: dict[str, float] = {}

    def poll(self, now: _dt.date | None = None) -> list[MemoryEvent]:
        if not self.config.enabled:
            return []

        today = now or _dt.date.today()
        events: list[MemoryEvent] = []
        events.extend(self._date_events(today))
        memory_event = self._memory_lookup()
        if memory_event:
            events.append(memory_event)
        return [event for event in events if self._can_emit(event.event_id)]

    def mark_emitted(self, event: MemoryEvent) -> None:
        self._last_emit[event.event_id] = time.monotonic()

    def _date_events(self, today: _dt.date) -> list[MemoryEvent]:
        result: list[MemoryEvent] = []
        for index, item in enumerate(self.config.date_events or []):
            date_text = str(item.get("date", "")).strip()
            if not _date_matches(date_text, today):
                continue
            label = str(item.get("label", "special day")).strip()
            note = str(item.get("note", "")).strip()
            context = f"Today is {label}."
            if note:
                context += f" {note}"
            result.append(
                MemoryEvent(
                    score=self.config.date_score,
                    context=context,
                    source="date",
                    event_id=f"date:{date_text}:{index}",
                )
            )
        return result

    def _memory_lookup(self) -> MemoryEvent | None:
        if not getattr(self.memory_backend, "ready", False):
            return None
        query = self.config.query.strip()
        if not query:
            return None
        try:
            context = self.memory_backend.get_context(query, user_id=self.user_id)
        except Exception:
            return None
        context = _compact_context(context)
        if not context:
            return None
        return MemoryEvent(
            score=self.config.memory_score,
            context=prompts.format_prompt("memory_events.memory_lookup", context=context),
            source="memory",
            event_id=f"memory:{_stable_key(context)}",
        )

    def _can_emit(self, event_id: str) -> bool:
        last = self._last_emit.get(event_id, 0.0)
        return time.monotonic() - last >= self.config.cooldown_seconds


def from_config(config: dict, memory_backend: object, user_id: str) -> MemoryEventDetector:
    section = config.get("impulse", {})
    if not isinstance(section, dict):
        section = {}

    def number(key: str, default: float) -> float:
        try:
            return float(section.get(key, default))
        except (TypeError, ValueError):
            return default

    date_events = section.get("memory_date_events", [])
    if not isinstance(date_events, list):
        date_events = []

    event_config = MemoryEventConfig(
        enabled=bool(section.get("memory_events_enabled", False)),
        check_interval=max(30.0, number("memory_check_interval", 300.0)),
        cooldown_seconds=max(60.0, number("memory_cooldown_seconds", 21600.0)),
        date_score=max(0.0, number("memory_date_score", 50.0)),
        memory_score=max(0.0, number("memory_lookup_score", 70.0)),
        query=str(section.get("memory_lookup_query", MemoryEventConfig.query)),
        date_events=[item for item in date_events if isinstance(item, dict)],
    )
    return MemoryEventDetector(memory_backend, user_id, event_config)


# ═══════════════════════════════════════════════════════════════════════════════
# Memory event store — structured event extraction, caching, summarization
# ═══════════════════════════════════════════════════════════════════════════════
# Replaces raw conversation-pair storage. Each turn, LLM extracts structured
# events (desc + tags) which accumulate in a pending buffer. Periodically,
# the LLM merges and deduplicates: stable events go to the vector store,
# active ones stay in cache. On shutdown everything flushes to mem0.

import json
import logging
from dataclasses import dataclass as _dataclass, field as _field

from kokoro import config as _cfg
from kokoro import prompts as _prompts

_logger = logging.getLogger(__name__)


@_dataclass
class StoredEvent:
    desc: str
    tags: list[str] = _field(default_factory=list)
    created_at: float = 0.0


class MemoryEventStore:
    """Extract, cache, summarize, and persist structured memory events."""

    def __init__(self, memory_backend, user_id: str):
        section = _cfg.get("memory_events", {})
        if not isinstance(section, dict):
            section = {}
        self.enabled = bool(section.get("enabled", True))
        self.eval_interval = max(1, int(section.get("eval_interval", 3)))
        self.eval_model = str(section.get("eval_model", "") or _cfg.llm_model())

        self._memory_backend = memory_backend
        self._user_id = user_id
        self._pending: list[StoredEvent] = []
        self._cache: list[StoredEvent] = []
        self._counter = 0

    # ── public API ──────────────────────────────────────────────────────────

    def on_conversation_turn(self, user_text: str, assistant_text: str,
                              user_name: str = "你", character_name: str = "助手",
                              summary: str = "") -> None:
        """Extract events from a conversation turn and manage the cache cycle."""
        if not self.enabled:
            return

        events = self._extract_events(user_text, assistant_text, user_name, character_name, summary)
        if events:
            self._pending.extend(events)

        self._counter += 1
        if self._counter >= self.eval_interval:
            self._counter = 0
            self._summarize(user_name, character_name, summary)

    def flush_all(self, user_name: str = "你", character_name: str = "助手",
                   summary: str = "") -> None:
        """Flush all pending and cached events to the vector store."""
        if not self.enabled:
            return
        all_events = self._pending + self._cache
        if not all_events:
            return

        dedup: list[str] = []
        for event in all_events:
            self._write_event(event, _dedup_bucket=dedup)
        self._pending.clear()
        self._cache.clear()
        _logger.info("memory event flush: %d events written", len(all_events))

    # ── internal: LLM extraction ────────────────────────────────────────────

    def _extract_events(self, user_text: str, assistant_text: str,
                        user_name: str, character_name: str,
                        summary: str = "") -> list[StoredEvent]:
        system = _prompts.format_prompt(
            "memory_events.extract_system",
            name=character_name,
        )
        user_prompt = _prompts.format_prompt(
            "memory_events.extract_user",
            name=character_name,
            user_name=user_name,
            user_text=user_text,
            assistant_text=assistant_text,
            summary=summary or "（无）",
        )

        raw = self._call_llm(system, user_prompt, max_tokens=512)
        return self._parse_event_list(raw)

    def _summarize(self, user_name: str, character_name: str,
                   summary: str = "") -> None:
        """Merge pending + cache, decide what goes to stable storage vs stays."""
        if not self._pending and not self._cache:
            return

        pending_json = json.dumps(
            [{"desc": e.desc, "tags": e.tags} for e in self._pending],
            ensure_ascii=False,
        )
        cache_json = json.dumps(
            [{"desc": e.desc, "tags": e.tags} for e in self._cache],
            ensure_ascii=False,
        )

        system = _prompts.format_prompt(
            "memory_events.summarize_system",
            name=character_name,
        )
        user_prompt = _prompts.format_prompt(
            "memory_events.summarize_user",
            pending_events=pending_json,
            summary_cache=cache_json,
            summary=summary or "（无）",
        )
        raw = self._call_llm(system, user_prompt, max_tokens=1024)
        result = self._parse_summary_result(raw)
        if result is None:
            return

        stable, keep_cache = result

        dedup: list[str] = []
        for event in stable:
            self._write_event(event, _dedup_bucket=dedup)

        self._pending = []
        self._cache = keep_cache

        _logger.debug(
            "memory event summary: %d stable, %d cached",
            len(stable), len(keep_cache),
        )

    def _write_event(self, event: StoredEvent, _dedup_bucket: list[str] | None = None) -> None:
        """Store a single event to the mem0 backend with simple dedup."""
        if not self._memory_backend or not event.desc:
            return
        _mem = getattr(self._memory_backend, '_mem', None)
        if _mem is None:
            return

        # Simple dedup: skip if a similar desc exists in the current batch
        if _dedup_bucket is not None:
            if _is_duplicate(event.desc, _dedup_bucket):
                _logger.debug("dedup skipped: %s", event.desc[:60])
                return
            _dedup_bucket.append(_normalize_desc(event.desc))

        try:
            _mem.add(
                event.desc,
                user_id=self._user_id,
                metadata={"tags": event.tags},
                infer=False,
            )
        except Exception as exc:
            _logger.warning("memory event store failed: %s", exc)

    # ── LLM call ────────────────────────────────────────────────────────────

    def _call_llm(self, system: str, user_prompt: str, max_tokens: int = 512) -> str:
        from kokoro import token_usage

        model = self.eval_model
        url = _cfg.llm_url()
        api_key = ""
        openai_compatible = False
        if _cfg.is_deepseek_model(model):
            api_key = _cfg.deepseek_api_key()
            url = _cfg.deepseek_url()
            openai_compatible = True

        headers = {"Content-Type": "application/json"}
        if openai_compatible:
            headers["Authorization"] = f"Bearer {api_key}"
            base_url = url.rstrip("/")
            if not re.search(r"/v\d+$", base_url):
                base_url += "/v1"
            api_url = f"{base_url}/chat/completions"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": max_tokens,
            }
        else:
            api_url = f"{url}/api/chat"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": max_tokens},
            }

        try:
            import urllib.request
            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())

            if openai_compatible:
                usage = result.get("usage", {})
                pt = int(usage.get("prompt_tokens", 0))
                ct = int(usage.get("completion_tokens", 0))
                if pt or ct:
                    token_usage.record(model, "memory_event_extract", pt, ct)
                return result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            else:
                pt = int(result.get("prompt_eval_count", 0))
                ct = int(result.get("eval_count", 0))
                if pt or ct:
                    token_usage.record(model, "memory_event_extract", pt, ct)
                return result.get("message", {}).get("content", "").strip()
        except Exception as exc:
            _logger.warning("memory event LLM call failed: %s", exc)
            return ""

    # ── response parsing ────────────────────────────────────────────────────

    @staticmethod
    def _parse_event_list(raw: str) -> list[StoredEvent]:
        if not raw:
            return []
        data = _extract_json(raw)
        if not isinstance(data, list):
            return []
        now = time.time()
        events = []
        for item in data:
            if not isinstance(item, dict):
                continue
            desc = str(item.get("desc", "")).strip()
            if not desc:
                continue
            tags_raw = item.get("tags", [])
            if isinstance(tags_raw, list):
                tags = [str(t).strip() for t in tags_raw if t]
            else:
                tags = []
            events.append(StoredEvent(desc=desc, tags=tags, created_at=now))
        return events

    @staticmethod
    def _parse_summary_result(raw: str) -> tuple[list[StoredEvent], list[StoredEvent]] | None:
        if not raw:
            return None
        data = _extract_json(raw)
        if not isinstance(data, dict):
            return None
        now = time.time()

        def _parse_list(key: str) -> list[StoredEvent]:
            items = data.get(key, [])
            if not isinstance(items, list):
                return []
            result = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                desc = str(item.get("desc", "")).strip()
                if not desc:
                    continue
                tags_raw = item.get("tags", [])
                tags = [str(t).strip() for t in tags_raw if t] if isinstance(tags_raw, list) else []
                result.append(StoredEvent(desc=desc, tags=tags, created_at=now))
            return result

        stable = _parse_list("stable")
        cache = _parse_list("cache")
        return stable, cache


def _extract_json(text: str):
    """Extract first JSON value from text (handles code fences)."""
    stripped = text.strip()
    code_match = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
    if code_match:
        candidate = code_match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    stripped = re.sub(r"^[^{[]+", "", stripped)
    stripped = re.sub(r"[^}\]]+$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    depth = 0
    start = -1
    for i, ch in enumerate(stripped):
        if ch in ("[", "{"):
            if depth == 0:
                start = i
            depth += 1
        elif ch in ("]", "}"):
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(stripped[start:i + 1])
                except json.JSONDecodeError:
                    pass
    return None


# ── dedup helpers ────────────────────────────────────────────────────────────

def _normalize_desc(text: str) -> str:
    """Strip punctuation/spaces for dedup comparison."""
    text = re.sub(r"[^一-鿟\w]", "", text)
    return text.lower()[:60]


def _is_duplicate(desc: str, existing: list[str]) -> bool:
    """Check if desc overlaps significantly with any existing descriptor."""
    norm = _normalize_desc(desc)
    if not norm:
        return False
    for existing_norm in existing:
        if len(norm) >= 4 and (norm in existing_norm or existing_norm in norm):
            return True
    return False
