"""Local open-webSearch daemon client."""

from __future__ import annotations

import json
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


def format_search_result(query: str, result: dict[str, Any], *, max_chars: int = 1800) -> str:
    """Format a daemon response as compact material for the life context."""
    items = _extract_items(result)
    lines = ["[web_search_result]", f"query: {query}"]
    if not items:
        raw = json.dumps(result, ensure_ascii=False)[:max_chars]
        lines.append("unstructured_result:")
        lines.append(raw)
        return "\n".join(lines)[:max_chars].strip()

    lines.append(f"candidate_count: {len(items)}")
    lines.append("candidates:")
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
        line = f"{i}. title: {title[:120]}"
        if url:
            line += f"\n   url: {url[:180]}"
        if snippet:
            line += f"\n   note: {snippet[:160]}"
        lines.append(line)
    lines.append("boundary: material for this query only; unrelated titles are not new topics.")
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

