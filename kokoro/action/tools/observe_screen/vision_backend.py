"""Vision backend request routing."""

from __future__ import annotations

import logging
import os

from kokoro.core import config as cfg
from kokoro.core import token_usage
from kokoro.action.tools.observe_screen.vision_config import (
    DASHSCOPE_URL,
    DEFAULT_DASHSCOPE_MODEL,
    KEY_API_KEY,
    KEY_BACKEND,
    KEY_MODEL,
)

logger = logging.getLogger("vision")


def _build_messages(items: list[tuple[str, str]]) -> list[dict]:
    """Build a list of user messages, one per (image_uri, prompt) pair."""
    messages = []
    for image_uri, prompt in items:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_uri}},
            ],
        })
    return messages


def _call_ollama(items: list[tuple[str, str]], model: str, base_url: str, timeout: int, function: str = "vision") -> str:
    import requests

    api_url = base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": _build_messages(items),
        "stream": False,
    }
    resp = requests.post(api_url, json=payload, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"Ollama API error {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    usage = data.get("usage", {})
    pt = int(usage.get("prompt_tokens", 0))
    ct = int(usage.get("completion_tokens", 0))
    if pt or ct:
        token_usage.record(model, function, pt, ct)
    return data["choices"][0]["message"]["content"]


def _call_dashscope(items: list[tuple[str, str]], model: str, api_key: str, timeout: int, function: str = "vision") -> str:
    import requests

    payload = {
        "model": model,
        "messages": _build_messages(items),
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    resp = requests.post(DASHSCOPE_URL, json=payload, headers=headers, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"DashScope API error {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    usage = data.get("usage", {})
    pt = int(usage.get("prompt_tokens", 0))
    ct = int(usage.get("completion_tokens", 0))
    if pt or ct:
        token_usage.record(model, function, pt, ct)
    return data["choices"][0]["message"]["content"]


def _vision_result(items: list[tuple[str, str]], model: str, backend: str,
                   base_url: str | None, api_key: str | None, timeout: int,
                   function: str = "vision") -> str:
    """Route a batch of (image_uri, prompt) pairs to the correct backend.

    Each pair becomes a separate user message in a single API request.
    Returns the model's combined text response.
    """
    conf = cfg.load()

    if backend == "ollama":
        url = (base_url or cfg.llm_url()).rstrip("/")
        logger.info("ollama %s  %s  items=%d  timeout=%ss  function=%s", model, url, len(items), timeout, function)
        return _call_ollama(items, model, url, timeout, function=function)

    key = api_key or conf.get(KEY_API_KEY) or os.environ.get("DASHSCOPE_API_KEY") or ""
    if not key:
        raise RuntimeError(
            "DashScope API key not set.  Provide --api-key, set config "
            f"'{KEY_API_KEY}', or export DASHSCOPE_API_KEY."
        )
    logger.info("dashscope %s  items=%d  timeout=%ss  function=%s", model, len(items), timeout, function)
    return _call_dashscope(items, model, key, timeout, function=function)
