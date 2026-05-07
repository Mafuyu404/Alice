"""Shared OpenAI-compatible LLM client helpers."""

from __future__ import annotations

import json
import re
import threading
from typing import Iterable, Optional

import requests

from kokoro import config as cfg


def api_headers(model: str) -> dict[str, str]:
    if cfg.is_deepseek_model(model):
        key = cfg.deepseek_api_key()
        if key:
            return {"Authorization": f"Bearer {key}"}
    return {}


def api_base_for(model: str) -> str:
    if cfg.is_deepseek_model(model):
        return cfg.deepseek_url() + "/v1"
    return cfg.api_base()


def upstream_url_for(model: str, prefer_kokoromemo: bool = False) -> str:
    if cfg.is_deepseek_model(model):
        return cfg.deepseek_url()
    if prefer_kokoromemo and cfg.kokoromo_url():
        return cfg.kokoromo_url()
    return cfg.llm_url()


def build_payload(model: str, messages: list[dict], stream: bool = True) -> dict:
    payload = {"model": model, "messages": messages, "stream": stream}
    if cfg.is_deepseek_model(model):
        payload["thinking"] = {"type": "disabled"}
    return payload


def parse_sse_delta(line: str) -> str:
    if not line or line == "data: [DONE]" or line.startswith(":"):
        return ""
    if not line.startswith("data: "):
        return ""
    try:
        chunk = json.loads(line[6:])
        return chunk["choices"][0].get("delta", {}).get("content", "")
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return ""


class _StreamCancelled(Exception):
    """Raised internally when a streaming request is cancelled."""
    pass


def stream_chat(
    messages: list[dict],
    model: str,
    timeout: int = 120,
    cancel_event: Optional[threading.Event] = None,
    api_base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Iterable[str]:
    base_url = api_base_for(model)
    if api_base_url:
        base_url = api_base_url.rstrip("/")
        if not re.search(r'/v\d+$', base_url):
            base_url += "/v1"
    headers = api_headers(model)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif not headers.get("Authorization") and cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg.get('api_key')}"
    resp = requests.post(
        f"{base_url}/chat/completions",
        json=build_payload(model, messages, stream=True),
        headers=headers,
        stream=True,
        timeout=timeout,
    )
    if not resp.ok:
        raise RuntimeError(f"API error {resp.status_code}: {resp.text[:200]}")

    resp.encoding = "utf-8"
    try:
        for line in resp.iter_lines(decode_unicode=True):
            if cancel_event and cancel_event.is_set():
                resp.close()
                return
            content = parse_sse_delta(line)
            if content:
                yield content
    finally:
        if cancel_event and cancel_event.is_set():
            resp.close()
