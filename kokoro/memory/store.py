"""SQLite store for Alice-owned memory records."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from kokoro.memory.models import MemoryRecord, MemoryRecordDraft, clamp01, new_id, now_iso


class MemoryStore:
    def __init__(self, *, character_id: str, root: Path) -> None:
        self.character_id = character_id
        self.root = Path(root)
        self.path = self.root / "characters" / character_id / "memory" / "store.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def write(self, draft: MemoryRecordDraft) -> MemoryRecord:
        record_id = new_id("mem")
        now = now_iso()
        keywords = _clean_list(draft.keywords) or _keywords(draft.content)
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                """
                insert into memory_records (
                  id, character_id, record_form, content, summary, created_at, updated_at,
                  last_accessed_at, last_diffused_at, access_count, direct_access_count,
                  diffused_access_count, importance, emotional_impact, keywords_json,
                  tags_json, source_event_ids_json, evidence_json, related_memory_ids_json,
                  metadata_json, index_status, deleted_at
                ) values (?, ?, ?, ?, ?, ?, ?, '', '', 0, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '')
                """,
                    (
                        record_id,
                        draft.character_id,
                        draft.record_form,
                        draft.content.strip(),
                        draft.summary.strip(),
                        now,
                        now,
                        clamp01(draft.importance, 0.5),
                        max(-1.0, min(1.0, float(draft.emotional_impact or 0.0))),
                        json.dumps(keywords, ensure_ascii=False),
                        json.dumps(_clean_list(draft.tags), ensure_ascii=False),
                        json.dumps(_clean_list(draft.source_event_ids), ensure_ascii=False),
                        json.dumps(draft.evidence or [], ensure_ascii=False, default=str),
                        json.dumps(_clean_list(draft.related_memory_ids), ensure_ascii=False),
                        json.dumps(draft.metadata or {}, ensure_ascii=False, default=str),
                    ),
                )
        self._link_new_record(record_id)
        return self.get(record_id)  # type: ignore[return-value]

    def write_or_merge(self, draft: MemoryRecordDraft, *, recent_limit: int = 24) -> tuple[MemoryRecord, bool]:
        target = self._find_similar_recent(draft, limit=recent_limit)
        if target is None:
            return self.write(draft), False
        return self.merge(target.id, draft), True

    def merge(self, record_id: str, draft: MemoryRecordDraft) -> MemoryRecord:
        current = self.get(record_id)
        if current is None:
            return self.write(draft)
        now = now_iso()
        keywords = _merge_lists(current.keywords, draft.keywords, _keywords(draft.content))
        tags = _merge_lists(current.tags, draft.tags)
        source_event_ids = _merge_lists(current.source_event_ids, draft.source_event_ids)
        related_memory_ids = _merge_lists(current.related_memory_ids, draft.related_memory_ids)
        evidence = _merge_evidence(current.evidence, draft.evidence)
        metadata = dict(current.metadata or {})
        metadata.update(draft.metadata or {})
        content = _merge_text(current.content, draft.content, max_chars=1600)
        summary = _merge_summary(current.summary, draft.summary, content)
        emotional = current.emotional_impact
        if abs(float(draft.emotional_impact or 0.0)) > abs(emotional):
            emotional = float(draft.emotional_impact or 0.0)
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    update memory_records
                    set content = ?,
                        summary = ?,
                        updated_at = ?,
                        importance = ?,
                        emotional_impact = ?,
                        keywords_json = ?,
                        tags_json = ?,
                        source_event_ids_json = ?,
                        evidence_json = ?,
                        related_memory_ids_json = ?,
                        metadata_json = ?,
                        index_status = 'pending'
                    where id = ? and deleted_at = ''
                    """,
                    (
                        content,
                        summary,
                        now,
                        max(clamp01(current.importance, 0.5), clamp01(draft.importance, 0.5)),
                        max(-1.0, min(1.0, emotional)),
                        json.dumps(keywords, ensure_ascii=False),
                        json.dumps(tags, ensure_ascii=False),
                        json.dumps(source_event_ids, ensure_ascii=False),
                        json.dumps(evidence, ensure_ascii=False, default=str),
                        json.dumps(related_memory_ids, ensure_ascii=False),
                        json.dumps(metadata, ensure_ascii=False, default=str),
                        record_id,
                    ),
                )
        return self.get(record_id)  # type: ignore[return-value]

    def get(self, record_id: str) -> MemoryRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute("select * from memory_records where id = ? and deleted_at = ''", (record_id,)).fetchone()
        return MemoryRecord.from_row(row) if row else None

    def search(self, query: str, *, limit: int = 8, use_access: bool = True) -> list[MemoryRecord]:
        terms = _query_terms(query)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "select * from memory_records where character_id = ? and deleted_at = ''",
                (self.character_id,),
            ).fetchall()
        records = [MemoryRecord.from_row(row) for row in rows]
        term_weights = _term_weights(terms, records)
        now_ranked: list[MemoryRecord] = []
        for record in records:
            record.score = self._score(record, terms, term_weights, use_access=use_access)
            if record.score > 0:
                now_ranked.append(record)
        now_ranked.sort(key=lambda item: item.score, reverse=True)
        return now_ranked[: max(1, int(limit))]

    def recent(self, *, limit: int = 8) -> list[MemoryRecord]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                select * from memory_records
                where character_id = ? and deleted_at = ''
                order by created_at desc
                limit ?
                """,
                (self.character_id, max(1, int(limit))),
            ).fetchall()
        return [MemoryRecord.from_row(row) for row in rows]

    def _find_similar_recent(self, draft: MemoryRecordDraft, *, limit: int) -> MemoryRecord | None:
        recent = self.recent(limit=limit)
        best: tuple[float, MemoryRecord] | None = None
        for record in recent:
            if record.record_form != draft.record_form:
                continue
            score = _record_similarity(record, draft)
            if score >= 0.78 and (best is None or score > best[0]):
                best = (score, record)
        return best[1] if best else None

    def link(self, from_id: str, to_id: str, *, link_type: str, weight: float = 0.5) -> None:
        if not from_id or not to_id or from_id == to_id:
            return
        now = now_iso()
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                """
                insert or ignore into memory_links (
                  id, character_id, from_memory_id, to_memory_id, link_type, weight, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (new_id("lnk"), self.character_id, from_id, to_id, link_type, clamp01(weight, 0.5), now, now),
                )

    def neighbors(self, record_id: str, *, limit: int = 4) -> list[MemoryRecord]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                select r.*, l.weight as link_weight
                from memory_links l
                join memory_records r on r.id = l.to_memory_id
                where l.character_id = ? and l.from_memory_id = ? and r.deleted_at = ''
                order by l.weight desc, r.created_at desc
                limit ?
                """,
                (self.character_id, record_id, max(1, int(limit))),
            ).fetchall()
        records = []
        for row in rows:
            record = MemoryRecord.from_row(row)
            record.score = float(row["link_weight"] or 0.0)
            records.append(record)
        return records

    def record_access(self, record_ids: list[str], *, diffuse: bool = True) -> None:
        now = now_iso()
        with closing(self._connect()) as conn:
            with conn:
                for record_id in dict.fromkeys(record_ids):
                    conn.execute(
                    """
                    update memory_records
                    set access_count = access_count + 1,
                        direct_access_count = direct_access_count + 1,
                        last_accessed_at = ?,
                        updated_at = ?
                    where id = ? and deleted_at = ''
                    """,
                        (now, now, record_id),
                    )
                    if not diffuse:
                        continue
                    links = conn.execute(
                        "select to_memory_id, weight from memory_links where character_id = ? and from_memory_id = ?",
                        (self.character_id, record_id),
                    ).fetchall()
                    for link in links:
                        inc = 0.35 * float(link["weight"] or 0.0)
                        if inc <= 0:
                            continue
                        conn.execute(
                            """
                            update memory_records
                            set access_count = access_count + ?,
                                diffused_access_count = diffused_access_count + ?,
                                last_diffused_at = ?,
                                updated_at = ?
                            where id = ? and deleted_at = ''
                            """,
                            (inc, inc, now, now, link["to_memory_id"]),
                        )

    def _score(
        self,
        record: MemoryRecord,
        terms: list[str],
        term_weights: dict[str, float],
        *,
        use_access: bool = True,
    ) -> float:
        text = " ".join([record.content, record.summary, " ".join(record.keywords), " ".join(record.tags)]).lower()
        if not terms:
            lexical = 0.1
        else:
            hit_strength = sum(term_weights.get(term, 1.0) for term in terms if term.lower() in text)
            lexical = hit_strength / max(4.0, min(18.0, sum(term_weights.values())))
        if lexical <= 0 and record.record_form != "open_thread":
            return 0.0
        if lexical <= 0:
            return 0.03 if record.record_form == "open_thread" else 0.0
        access = min(0.12, math.log1p(record.access_count) * 0.025) if use_access else 0.0
        importance = clamp01(record.importance, 0.5) * 0.18
        emotional = abs(record.emotional_impact) * 0.06
        open_thread = 0.04 if record.record_form == "open_thread" else 0.0
        return lexical * (1.0 + importance + emotional + access) + open_thread

    def _link_new_record(self, record_id: str) -> None:
        recent = self.recent(limit=8)
        for other in recent:
            if other.id == record_id:
                continue
            self.link(record_id, other.id, link_type="temporal_near", weight=0.35)
            self.link(other.id, record_id, link_type="temporal_near", weight=0.25)

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                """
                create table if not exists memory_records (
                  id text primary key,
                  character_id text not null,
                  record_form text not null,
                  content text not null,
                  summary text not null default '',
                  created_at text not null,
                  updated_at text not null,
                  last_accessed_at text not null default '',
                  last_diffused_at text not null default '',
                  access_count real not null default 0,
                  direct_access_count real not null default 0,
                  diffused_access_count real not null default 0,
                  importance real not null default 0.5,
                  emotional_impact real not null default 0,
                  keywords_json text not null default '[]',
                  tags_json text not null default '[]',
                  source_event_ids_json text not null default '[]',
                  evidence_json text not null default '[]',
                  related_memory_ids_json text not null default '[]',
                  metadata_json text not null default '{}',
                  index_status text not null default 'pending',
                  deleted_at text not null default ''
                )
                """
            )
                conn.execute(
                """
                create table if not exists memory_links (
                  id text primary key,
                  character_id text not null,
                  from_memory_id text not null,
                  to_memory_id text not null,
                  link_type text not null,
                  weight real not null default 0.5,
                  created_at text not null,
                  updated_at text not null,
                  unique(character_id, from_memory_id, to_memory_id, link_type)
                )
                """
            )
                conn.execute("create index if not exists idx_memory_records_character on memory_records(character_id)")
                conn.execute("create index if not exists idx_memory_links_from on memory_links(character_id, from_memory_id)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _clean_list(values: list[Any]) -> list[str]:
    result = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result[:24]


def _merge_lists(*groups: list[Any]) -> list[str]:
    result: list[str] = []
    for group in groups:
        for value in group or []:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
    return result[:32]


def _merge_evidence(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*(left or []), *(right or [])]:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[:24]


def _merge_text(left: str, right: str, *, max_chars: int) -> str:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not right_text:
        return left_text[-max_chars:].strip()
    if not left_text:
        return right_text[-max_chars:].strip()
    compact_left = re.sub(r"\s+", "", left_text)
    compact_right = re.sub(r"\s+", "", right_text)
    if compact_right in compact_left:
        return left_text[-max_chars:].strip()
    if compact_left in compact_right:
        return right_text[-max_chars:].strip()
    merged = f"{left_text}\n{right_text}"
    return merged[-max_chars:].strip()


def _merge_summary(left: str, right: str, content: str) -> str:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    if not left_text:
        return right_text or content[:120]
    if not right_text:
        return left_text
    compact_left = re.sub(r"\s+", "", left_text)
    compact_right = re.sub(r"\s+", "", right_text)
    if compact_right in compact_left:
        return left_text
    if compact_left in compact_right:
        return right_text
    return f"{left_text}；{right_text}"[:180]


def _record_similarity(record: MemoryRecord, draft: MemoryRecordDraft) -> float:
    left = " ".join([record.content, record.summary, " ".join(record.keywords), " ".join(record.tags)])
    right = " ".join([draft.content, draft.summary, " ".join(draft.keywords), " ".join(draft.tags)])
    compact_left = re.sub(r"\s+", "", left)
    compact_right = re.sub(r"\s+", "", right)
    if not compact_left or not compact_right:
        return 0.0
    shorter = min(len(compact_left), len(compact_right))
    if shorter >= 24 and (compact_left in compact_right or compact_right in compact_left):
        return 1.0
    terms_left = set(_query_terms(left))
    terms_right = set(_query_terms(right))
    if terms_left and terms_right:
        term_score = len(terms_left & terms_right) / max(1, min(len(terms_left), len(terms_right)))
    else:
        term_score = 0.0
    gram_score = _ngram_containment(compact_left, compact_right)
    keyword_score = 0.0
    if str(record.summary or "").strip() and str(draft.summary or "").strip():
        keyword_score = _keyword_overlap(record.keywords, draft.keywords)
    return max(term_score, gram_score, keyword_score)


def _keyword_overlap(left: list[str], right: list[str]) -> float:
    left_keywords = {item for item in _clean_list(left) if len(item) >= 3}
    right_keywords = {item for item in _clean_list(right) if len(item) >= 3}
    if not left_keywords or not right_keywords:
        return 0.0
    shared = left_keywords & right_keywords
    if not shared:
        return 0.0
    longest = max(len(item) for item in shared)
    base = len(shared) / max(1, min(len(left_keywords), len(right_keywords)))
    if longest >= 6:
        return max(0.82, base)
    if longest >= 4:
        return max(0.72, base)
    return base


def _ngram_containment(left: str, right: str, *, size: int = 2) -> float:
    left_grams = _char_ngrams(left, size=size)
    right_grams = _char_ngrams(right, size=size)
    if not left_grams or not right_grams:
        return 0.0
    smaller = left_grams if len(left_grams) <= len(right_grams) else right_grams
    larger = right_grams if smaller is left_grams else left_grams
    return len(smaller & larger) / max(1, len(smaller))


def _char_ngrams(text: str, *, size: int) -> set[str]:
    compact = re.sub(r"\s+", "", str(text or ""))
    if len(compact) < size:
        return set()
    return {compact[index : index + size] for index in range(0, len(compact) - size + 1)}


def _keywords(text: str) -> list[str]:
    raw = _text_terms(text)
    result: list[str] = []
    for item in raw:
        if item not in result:
            result.append(item)
    return result[:24]


def _query_terms(text: str) -> list[str]:
    raw = _text_terms(text)
    terms: list[str] = []
    for item in raw:
        if item not in terms:
            terms.append(item)
    for token in list(terms):
        for gram in _cjk_ngrams(token):
            if gram not in terms:
                terms.append(gram)
    return terms[:128]


def _text_terms(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{2,}", str(text or "").lower())


def _cjk_ngrams(text: str) -> list[str]:
    grams: list[str] = []
    for match in re.finditer(r"[\u4e00-\u9fff]{4,}", str(text or "")):
        chars = match.group(0)
        for size in (2, 3):
            for index in range(0, max(0, len(chars) - size + 1)):
                gram = chars[index : index + size]
                if gram not in grams:
                    grams.append(gram)
    return grams


def _term_weights(terms: list[str], records: list[MemoryRecord]) -> dict[str, float]:
    if not terms:
        return {}
    texts = [
        " ".join([record.content, record.summary, " ".join(record.keywords), " ".join(record.tags)]).lower()
        for record in records
    ]
    total = max(1, len(texts))
    weights: dict[str, float] = {}
    for term in terms:
        lowered = term.lower()
        doc_hits = sum(1 for text in texts if lowered in text)
        rarity = math.log((total + 1) / (doc_hits + 1)) + 1.0
        weights[term] = _term_specificity(term) * rarity
    return weights


def _term_specificity(term: str) -> float:
    text = str(term or "")
    if re.fullmatch(r"[\u4e00-\u9fff]+", text):
        if len(text) <= 2:
            return 0.45
        if len(text) == 3:
            return 0.9
        return min(2.2, 1.2 + len(text) * 0.12)
    return min(2.0, 0.7 + len(text) * 0.08)
