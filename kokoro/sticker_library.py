"""Local sticker library for QQ expression images."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from collections import Counter
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = _ROOT / "data" / "stickers"


class StickerLibrary:
    def __init__(self, base_dir: str | os.PathLike | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else _DEFAULT_DIR
        self.image_dir = self.base_dir / "images"
        self.manifest_path = self.base_dir / "manifest.json"
        self.image_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[dict[str, Any]]:
        if not self.manifest_path.exists():
            return []
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def save_manifest(self, items: list[dict[str, Any]]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def update_item(self, sticker_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        items = self.load()
        for item in items:
            if str(item.get("id") or "") == str(sticker_id or ""):
                for key, value in (updates or {}).items():
                    if value in ("", None, []):
                        continue
                    item[key] = value
                self.save_manifest(items)
                return item
        return None

    def add_from_file(
        self,
        image_path: str | os.PathLike,
        *,
        desc: str,
        tags: list[str] | None = None,
        why_saved: str = "",
        source: str = "qq",
        source_group: str = "",
        source_sender: str = "",
        kind: str = "sticker",
        fingerprint: str = "",
    ) -> dict[str, Any]:
        src = Path(image_path)
        suffix = src.suffix.lower() or ".png"
        stem = _slug(tags[0] if tags else desc) or "sticker"
        sticker_id = f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        dest = self.image_dir / f"{sticker_id}{suffix}"
        shutil.copy2(src, dest)
        item = {
            "id": sticker_id,
            "path": str(dest.relative_to(self.base_dir)).replace("\\", "/"),
            "desc": str(desc or "").strip(),
            "tags": [str(t).strip() for t in (tags or []) if str(t).strip()][:8],
            "text": "",
            "emotions": [],
            "scenes": [],
            "style": [],
            "intensity": "",
            "avoid": "",
            "usage_notes": "",
            "why_saved": str(why_saved or "").strip(),
            "source": source,
            "source_group": source_group,
            "source_sender": source_sender,
            "kind": kind,
            "fingerprint": str(fingerprint or "").strip(),
            "created_at": datetime.now().astimezone().isoformat(),
        }
        items = self.load()
        items.append(item)
        self.save_manifest(items)
        return item

    def resolve_path(self, sticker_id: str) -> str:
        item = self.resolve_item(sticker_id)
        if item:
            path = self.base_dir / str(item.get("path") or "")
            return str(path.resolve())
        return ""

    def resolve_item(self, sticker_id: str) -> dict[str, Any] | None:
        wanted = str(sticker_id or "").strip()
        if not wanted:
            return None
        items = self.load()
        for item in items:
            if str(item.get("id") or "") == wanted:
                return item
        prefix_matches = [item for item in items if str(item.get("id") or "").startswith(wanted)]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        return None

    def fallback_item(self, query: str = "", *, min_score: float = 0.45) -> dict[str, Any] | None:
        items = self.load()
        if not items:
            return None
        query = str(query or "").strip()
        if not query:
            return None
        query_l = query.lower()
        best_item: dict[str, Any] | None = None
        best_score = 0.0
        for item in items:
            text = _item_search_text(item)
            score = SequenceMatcher(None, query_l[:240], text[:240]).ratio()
            if query_l and query_l in text:
                score += 0.35
            for term, weight in _terms(query_l).items():
                if term in text:
                    score += min(0.25, 0.04 * weight)
            if score > best_score:
                best_score = score
                best_item = item
        if best_item and best_score >= min_score:
            return best_item
        return None

    def candidates_text(self, *, limit: int = 18) -> str:
        items = self.load()[-max(1, limit):]
        return self.format_candidates(items)

    def search_candidates(self, query: str, *, limit: int = 30) -> str:
        return self.format_candidates(self.search_items(query, limit=limit))

    def search_items(self, query: str, *, limit: int = 30) -> list[dict[str, Any]]:
        items = self.load()
        if not items:
            return []
        terms = _terms(query)
        if not terms:
            return items[-max(1, limit):]
        scored: list[tuple[float, int, dict[str, Any]]] = []
        query_text = " ".join(terms)
        for index, item in enumerate(items):
            text = _item_search_text(item)
            score = 0.0
            for term, weight in terms.items():
                if term in text:
                    score += 3.0 * weight
            score += SequenceMatcher(None, query_text[:300], text[:500]).ratio()
            score += min(0.5, index / max(1, len(items)) * 0.5)
            if score > 0:
                scored.append((score, index, item))
        if not scored:
            return items[-max(1, limit):]
        scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        return [item for _, _, item in scored[: max(1, limit)]]

    def format_candidates(self, items: list[dict[str, Any]]) -> str:
        if not items:
            return "(no stickers)"
        return "\n".join(_format_item(item) for item in items)

    def find_similar(
        self,
        *,
        desc: str = "",
        tags: list[str] | None = None,
        fingerprint: str = "",
        threshold: float = 0.82,
    ) -> dict[str, Any] | None:
        desc = str(desc or "").strip()
        tag_set = {str(t).strip() for t in (tags or []) if str(t).strip()}
        fingerprint = str(fingerprint or "").strip()
        for item in reversed(self.load()):
            if fingerprint and fingerprint == str(item.get("fingerprint") or ""):
                return item
            item_desc = str(item.get("desc") or "")
            if desc and item_desc:
                ratio = SequenceMatcher(None, desc[:240], item_desc[:240]).ratio()
                item_tags = {str(t).strip() for t in (item.get("tags") or []) if str(t).strip()}
                overlap = len(tag_set & item_tags)
                if ratio >= threshold or (ratio >= 0.68 and overlap >= 2):
                    return item
        return None


def _slug(text: str) -> str:
    value = re.sub(r"\s+", "_", str(text or "").strip().lower())
    value = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "", value)
    return value[:24].strip("_-")


def _format_item(item: dict[str, Any]) -> str:
    fields = [
        f"id={item.get('id')}",
        f"desc={str(item.get('desc') or '')[:100]}",
    ]
    text = str(item.get("text") or "").strip()
    if text:
        fields.append(f"text={text[:40]}")
    for key in ("emotions", "scenes", "style", "tags"):
        values = [str(v).strip() for v in (item.get(key) or []) if str(v).strip()]
        if values:
            fields.append(f"{key}={','.join(values[:6])}")
    if item.get("intensity"):
        fields.append(f"intensity={str(item.get('intensity'))[:24]}")
    if item.get("usage_notes"):
        fields.append(f"use={str(item.get('usage_notes'))[:80]}")
    return "- " + " | ".join(fields)


def _item_search_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("id", ""),
        item.get("desc", ""),
        item.get("text", ""),
        item.get("why_saved", ""),
        item.get("usage_notes", ""),
        item.get("intensity", ""),
        item.get("avoid", ""),
    ]
    for key in ("tags", "emotions", "scenes", "style"):
        parts.extend(str(v) for v in (item.get(key) or []))
    return " ".join(str(part or "") for part in parts).lower()


def _terms(text: str) -> Counter:
    raw = str(text or "").lower()
    words = re.findall(r"[a-z0-9_\-]{2,}|[\u4e00-\u9fff]{1,6}", raw)
    stop = {"的", "了", "是", "我", "你", "他", "她", "它", "在", "和", "就", "都", "很", "吗", "呢"}
    return Counter(word for word in words if word not in stop)
