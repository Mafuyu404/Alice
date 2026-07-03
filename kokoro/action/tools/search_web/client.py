"""Local open-webSearch daemon client."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any


class WebSearchClient:
    def __init__(self, base_url: str = "http://127.0.0.1:3000", timeout: float = 45.0):
        self.base_url = str(base_url or "http://127.0.0.1:3000").rstrip("/")
        self.timeout = float(timeout or 45.0)

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def search(self, query: str, *, limit: int = 5, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": str(query or "").strip(),
            "limit": int(limit or 5),
        }
        payload.update({k: v for k, v in extra.items() if v not in (None, "", [], {})})
        return self._request("POST", "/search", payload)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {raw[:500]}") from exc
        if not raw.strip():
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
        return value if isinstance(value, dict) else {"data": value}


def create_client(section: dict | None = None) -> WebSearchClient:
    section = section if isinstance(section, dict) else {}
    return WebSearchClient(
        base_url=str(section.get("base_url") or "http://127.0.0.1:3000"),
        timeout=float(section.get("timeout", 45.0) or 45.0),
    )


def format_search_result(query: str, result: dict[str, Any], *, max_chars: int = 6000) -> str:
    """Format a daemon response as a compact external input event."""
    items = _extract_items(result)
    lines = [f"我刚刚搜索了：{query}"]
    if not items:
        raw = json.dumps(result, ensure_ascii=False)[:max_chars]
        lines.append("搜索返回了结果，但结构无法简化：")
        lines.append(raw)
        return "\n".join(lines)[:max_chars].strip()

    lines.append("搜索结果：")
    matching = _matching_items(query, items)
    if matching:
        titles = "；".join(str(item.get("title") or item.get("name") or "")[:80] for item in matching[:3])
        lines.append(f"命中提示：以下结果看起来直接包含查询词或其关键词：{titles}")
    for i, item in enumerate(items, 1):
        title = str(item.get("title") or item.get("name") or "无标题").strip()
        url = str(item.get("url") or item.get("link") or item.get("href") or "").strip()
        snippet = str(
            item.get("snippet")
            or item.get("content")
            or item.get("description")
            or item.get("summary")
            or ""
        ).strip()
        line = f"{i}. {title}"
        if url:
            line += f"\n   {url}"
        if snippet:
            line += f"\n   {snippet[:500]}"
        lines.append(line)
    return "\n".join(lines)[:max_chars].strip()


def _extract_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if not isinstance(value, dict):
        return []
    if isinstance(value.get("data"), dict):
        nested = _extract_items(value["data"])
        if nested:
            return nested
    for key in ("results", "items", "data", "organic", "webPages"):
        candidate = value.get(key)
        if isinstance(candidate, list):
            return [x for x in candidate if isinstance(x, dict)]
        if isinstance(candidate, dict):
            nested = _extract_items(candidate)
            if nested:
                return nested
    return []


def _matching_items(query: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query_text = str(query or "").strip().lower()
    compact_query = re.sub(r"\s+", "", query_text)
    tokens = [token for token in re.split(r"\s+", query_text) if len(token) >= 2]
    matched: list[dict[str, Any]] = []
    for item in items:
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("title", "name", "url", "link", "href", "source", "snippet", "content", "description", "summary")
        ).lower()
        compact_haystack = re.sub(r"\s+", "", haystack)
        if compact_query and compact_query in compact_haystack:
            matched.append(item)
            continue
        if tokens and any(len(token) >= 4 and token in haystack for token in tokens):
            matched.append(item)
            continue
        if tokens and all(token in haystack for token in tokens[:4]):
            matched.append(item)
    return matched
