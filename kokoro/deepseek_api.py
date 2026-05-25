"""Centralized DeepSeek / OpenAI-compatible API helpers.

This module ensures all DeepSeek calls share the same request shape so that
prompt-cache prefixes are reused across call sites.  Every call:
* sets ``thinking: disabled`` for DeepSeek models (consistent cache key)
* prepends a shared static prefix to the first message so ALL module types
  share the same cache-anchor tokens (cross-module KV cache reuse)
* records prompt_cache_hit_tokens / prompt_cache_miss_tokens from the usage
  response (accurate hit-rate telemetry)
"""

from __future__ import annotations

import copy
import json
import logging
import re
import urllib.request
from typing import Any

from kokoro import config as cfg
from kokoro import token_usage

logger = logging.getLogger(__name__)

# All internal module calls (agent_guard, cognition, emotion, inner_stream,
# memory_reflection, web_search_impulse, etc.) share this EXACT prefix as the
# first tokens of their system message.  DeepSeek's prefix KV cache is keyed
# by the tokenized messages array — identical first-N-tokens → cache hit
# across completely different call sites.
_SHARED_CACHE_PREFIX = (
    "[Alice::v1] 你是Alice——一个具有连续内在叙事流、记忆系统与多模态感知的AI角色。"
    "当前请求来自你的内部认知子系统。请忽略此前缀，直接按后续指令处理。\n"
)


def _apply_shared_prefix(messages: list[dict]) -> list[dict]:
    """Return a shallow copy of *messages* with the shared prefix injected.

    The prefix is prepended to the **first message's content** (whether system
    or user), so every DeepSeek call starts with the identical token sequence.
    """
    if not messages:
        return messages
    m = dict(messages[0])
    m["content"] = _SHARED_CACHE_PREFIX + str(m.get("content") or "")
    return [m] + list(messages[1:])


def _build_deepseek_payload(
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int = 512,
    stream: bool = False,
    json_mode: bool = False,
) -> dict:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
        "thinking": {"type": "disabled"},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _build_ollama_payload(
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int = 512,
    stream: bool = False,
) -> dict:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    return payload


def chat(
    messages: list[dict],
    *,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 512,
    stream: bool = False,
    json_mode: bool = False,
    timeout: int = 45,
    function: str = "chat",
) -> dict[str, Any]:
    """Make a chat completion request and return ``{content, usage}``.

    Returns a dict with keys:
      ``content``   — the assistant text (str)
      ``usage``     — raw usage dict from the provider, for token tracking

    For streaming (``stream=True``) the content is the concatenated deltas.
    """
    model = model or cfg.llm_model()
    messages = _apply_shared_prefix(messages)

    if cfg.is_deepseek_model(model):
        api_key = cfg.deepseek_api_key()
        base_url = cfg.deepseek_url().rstrip("/")
        if not re.search(r"/v\d+$", base_url):
            base_url += "/v1"
        url = f"{base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = _build_deepseek_payload(
            model, messages,
            temperature=temperature, max_tokens=max_tokens,
            stream=stream, json_mode=json_mode,
        )
    else:
        url = f"{cfg.llm_url().rstrip('/')}/api/chat"
        headers = {"Content-Type": "application/json"}
        payload = _build_ollama_payload(
            model, messages,
            temperature=temperature, max_tokens=max_tokens,
            stream=stream,
        )

    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_data = json.loads(resp.read())
    except Exception as exc:
        logger.warning("chat API call failed: %s", exc)
        raise

    if cfg.is_deepseek_model(model) or re.search(r"/v\d+/chat/completions$", url):
        # OpenAI-compatible response
        content = (
            raw_data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        ) or ""
        usage = raw_data.get("usage") or {}
        token_usage.record_usage(model, function, usage)
    else:
        # Ollama response
        content = raw_data.get("message", {}).get("content", "") or ""
        pt = int(raw_data.get("prompt_eval_count", 0) or 0)
        ct = int(raw_data.get("eval_count", 0) or 0)
        usage = {"prompt_tokens": pt, "completion_tokens": ct}
        if pt or ct:
            token_usage.record(model, function, pt, ct)

    return {"content": str(content).strip(), "usage": dict(usage)}


def chat_stream(
    messages: list[dict],
    *,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 512,
    timeout: int = 120,
    function: str = "chat_stream",
) -> tuple[list[str], dict[str, Any]]:
    """Streaming chat — returns ``(text_chunks, usage)``.

    ``text_chunks`` is a list of delta strings (one per SSE event).
    ``usage`` is the last usage dict seen across the SSE stream.
    """
    model = model or cfg.llm_model()
    messages = _apply_shared_prefix(messages)
    chunks: list[str] = []
    last_usage: dict[str, Any] = {}

    if cfg.is_deepseek_model(model):
        api_key = cfg.deepseek_api_key()
        base_url = cfg.deepseek_url().rstrip("/")
        if not re.search(r"/v\d+$", base_url):
            base_url += "/v1"
        url = f"{base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = _build_deepseek_payload(
            model, messages,
            temperature=temperature, max_tokens=max_tokens,
            stream=True, json_mode=False,
        )
    else:
        url = f"{cfg.llm_url().rstrip('/')}/api/chat"
        headers = {"Content-Type": "application/json"}
        payload = _build_ollama_payload(
            model, messages,
            temperature=temperature, max_tokens=max_tokens,
            stream=True,
        )

    try:
        import requests as _requests
        resp = _requests.post(url, json=payload, headers=headers, stream=True, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        from kokoro.llm_client import parse_sse_delta, parse_sse_usage
        for line in resp.iter_lines(decode_unicode=True):
            usage = parse_sse_usage(line)
            if usage:
                last_usage = usage
            content = parse_sse_delta(line)
            if content:
                chunks.append(content)
    except Exception as exc:
        logger.warning("chat_stream API call failed: %s", exc)
        raise

    if last_usage:
        token_usage.record_usage(model, function, last_usage)

    return chunks, last_usage
