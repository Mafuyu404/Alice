"""Periodic Microsoft Edge page text capture via DevTools Protocol."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import websockets

from kokoro.core import prompts
from kokoro.action import vision


_ROOT = Path(__file__).resolve().parent.parent
_EDGE_PROCESSES = {"msedge.exe", "msedge"}
_TITLE_SUFFIX_RE = re.compile(r"\s*[-–—]\s*(?:Microsoft Edge|Edge)\s*$", re.IGNORECASE)


@dataclass
class EdgeCacheConfig:
    enabled: bool = False
    interval_seconds: float = 15.0
    devtools_host: str = "127.0.0.1"
    devtools_port: int = 9222
    cache_file: str = "data/edge_page_cache.json"
    max_chars: int = 12000
    request_timeout: float = 3.0


class EdgeCacheError(RuntimeError):
    pass


class DevToolsClient:
    def __init__(self, websocket_url: str, timeout: float = 3.0):
        self.websocket_url = websocket_url
        self.timeout = timeout
        self._next_id = 1

    async def call(self, method: str, params: dict | None = None) -> dict:
        request_id = self._next_id
        self._next_id += 1
        payload = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params

        async with websockets.connect(self.websocket_url, open_timeout=self.timeout) as ws:
            await asyncio.wait_for(ws.send(json.dumps(payload)), timeout=self.timeout)
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                message = json.loads(raw)
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise EdgeCacheError(str(message["error"]))
                return message.get("result", {})


def config_from_dict(config: dict) -> EdgeCacheConfig:
    section = config.get("edge_page_cache", {})
    if not isinstance(section, dict):
        section = {}
    return EdgeCacheConfig(
        enabled=bool(section.get("enabled", False)),
        interval_seconds=max(1.0, float(section.get("interval_seconds", 15.0))),
        devtools_host=str(section.get("devtools_host", "127.0.0.1")),
        devtools_port=int(section.get("devtools_port", 9222)),
        cache_file=str(section.get("cache_file", "data/edge_page_cache.json")),
        max_chars=max(1000, int(section.get("max_chars", 12000))),
        request_timeout=max(1.0, float(section.get("request_timeout", 3.0))),
    )


def capture_and_save(config: EdgeCacheConfig) -> dict[str, Any]:
    payload = capture_current_page(config)
    write_cache(config.cache_file, payload)
    return payload


def capture_current_page(config: EdgeCacheConfig) -> dict[str, Any]:
    foreground = vision.get_foreground_app()
    tabs = list_tabs(config)
    active_tab = choose_tab(tabs, foreground, config)
    if active_tab is None:
        raise EdgeCacheError("No matching Edge page tab found")

    websocket_url = active_tab.get("webSocketDebuggerUrl")
    if not websocket_url:
        raise EdgeCacheError("Selected Edge tab does not expose a websocket debugger URL")

    expression = """
(() => {
  const title = document.title || "";
  const url = location.href || "";
  const text = document.body ? document.body.innerText : "";
  return { title, url, text };
})()
""".strip()
    result = asyncio.run(_evaluate(websocket_url, expression, config.request_timeout))
    value = result.get("value") if isinstance(result, dict) else None
    if not isinstance(value, dict):
        raise EdgeCacheError("Edge returned an unexpected page capture result")

    text = str(value.get("text", ""))
    return {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "edge_devtools",
        "foreground": foreground or {},
        "tab": {
            "id": str(active_tab.get("id") or ""),
            "title": str(value.get("title") or active_tab.get("title") or ""),
            "url": str(value.get("url") or active_tab.get("url") or ""),
        },
        "text": text[: config.max_chars],
        "text_truncated": len(text) > config.max_chars,
    }


def list_tabs(config: EdgeCacheConfig) -> list[dict[str, Any]]:
    url = f"http://{config.devtools_host}:{config.devtools_port}/json"
    try:
        response = requests.get(url, timeout=config.request_timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise EdgeCacheError(
            f"Cannot connect to Edge DevTools at {url}. Start Edge with --remote-debugging-port={config.devtools_port}."
        ) from exc

    data = response.json()
    if not isinstance(data, list):
        raise EdgeCacheError("Edge DevTools /json endpoint did not return a tab list")
    return [item for item in data if isinstance(item, dict) and item.get("type") == "page"]


def choose_tab(tabs: list[dict[str, Any]], foreground: dict | None, config: EdgeCacheConfig | None = None) -> dict[str, Any] | None:
    pages = [tab for tab in tabs if tab.get("webSocketDebuggerUrl")]
    if not pages:
        return None

    process = str((foreground or {}).get("process") or "").lower()
    title = str((foreground or {}).get("title") or "")
    if process in _EDGE_PROCESSES and title:
        normalized = _normalize_title(title)
        for tab in pages:
            if _normalize_title(str(tab.get("title") or "")) == normalized:
                return tab
        for tab in pages:
            tab_title = _normalize_title(str(tab.get("title") or ""))
            if normalized and (normalized in tab_title or tab_title in normalized):
                return tab

    previous = read_cache(config.cache_file) if config else None
    previous_tab = previous.get("tab") if isinstance(previous, dict) and isinstance(previous.get("tab"), dict) else {}
    previous_id = str(previous_tab.get("id") or "").strip()
    if previous_id:
        for tab in pages:
            if str(tab.get("id") or "").strip() == previous_id:
                return tab

    previous_url = str(previous_tab.get("url") or "").strip()
    if previous_url:
        for tab in pages:
            if str(tab.get("url") or "").strip() == previous_url:
                return tab

    mcmod_pages = [tab for tab in pages if "mcmod.cn" in str(tab.get("url") or "").lower()]
    if len(mcmod_pages) == 1:
        return mcmod_pages[0]
    if mcmod_pages:
        return mcmod_pages[0]

    return pages[0]


async def _evaluate(websocket_url: str, expression: str, timeout: float) -> dict[str, Any]:
    client = DevToolsClient(websocket_url, timeout=timeout)
    result = await client.call(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        },
    )
    return result.get("result", {})


def write_cache(path_value: str, payload: dict[str, Any]) -> None:
    path = Path(path_value)
    if not path.is_absolute():
        path = _ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_error_cache(path_value: str, error: str) -> None:
    write_cache(
        path_value,
        {
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": "edge_devtools",
            "error": error,
            "tab": {},
            "text": "",
            "text_truncated": False,
        },
    )


def read_cache(path_value: str) -> dict[str, Any] | None:
    path = Path(path_value)
    if not path.is_absolute():
        path = _ROOT / path
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def format_for_prompt(path_value: str, max_chars: int = 4000) -> str:
    data = read_cache(path_value)
    if not data:
        return ""
    if data.get("error"):
        return prompts.format_prompt("edge_cache.error_format", error=data.get('error', ''))

    tab = data.get("tab") if isinstance(data.get("tab"), dict) else {}
    title = str(tab.get("title") or "（无标题）")
    url = str(tab.get("url") or "")
    captured_at = str(data.get("captured_at") or "")
    text = str(data.get("text") or "").strip()
    if not text:
        return ""

    if len(text) > max_chars:
        text = text[:max_chars] + prompts.get("edge_cache.truncated_suffix", "\n...（已截断）")

    lines = [
        f"标题：{title}",
        f"URL：{url}" if url else "",
        f"抓取时间：{captured_at}" if captured_at else "",
        "正文：",
        text,
    ]
    return "\n".join(line for line in lines if line)


def _normalize_title(title: str) -> str:
    return _TITLE_SUFFIX_RE.sub("", title).strip().lower()
