"""Local/high-frequency LLM calls for life runtime prompt work."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from kokoro.core import config as cfg
from kokoro.core import deepseek_api
from kokoro.core import lifecycle_debug


class LocalThinking:
    """Thin prompt caller.

    It deliberately does not decide when thinking is important.  The prompts
    ask the LLM to decide; runtime only provides a fast model path.
    """

    def __init__(self, section: dict[str, Any] | None = None) -> None:
        section = dict(section or {})
        self.enabled = bool(section.get("enabled", True))
        self.model = str(section.get("model") or section.get("local_model") or cfg.stt_refine_model() or cfg.llm_model())
        self.base_url = str(section.get("base_url") or cfg.llm_url()).rstrip("/")
        self.api_style = str(section.get("api_style") or "auto").strip().lower() or "auto"
        self.api_key = str(section.get("api_key") or "").strip()
        self.timeout = int(section.get("timeout", 20) or 20)
        self.temperature = float(section.get("temperature", 0.4) or 0.4)

    def chat(self, messages: list[dict], options: dict[str, Any] | None = None) -> str:
        if not self.enabled:
            return ""
        options = dict(options or {})
        model = str(options.get("model") or self.model)
        max_tokens = int(options.get("max_tokens", 384) or 384)
        function = str(options.get("function") or "life_local_thinking")
        timeout = int(options.get("timeout", self.timeout))
        temperature = float(options.get("temperature", self.temperature))
        lifecycle_debug.log(
            "life.local_thinking.start",
            model=model,
            function=function,
            max_tokens=max_tokens,
            api_style=self.api_style,
            base_url=self.base_url,
        )
        if cfg.is_deepseek_model(model):
            result = deepseek_api.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                function=function,
            )
            content = str(result.get("content") or "").strip()
        else:
            content = self._chat_local(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        lifecycle_debug.log(
            "life.local_thinking.done",
            model=model,
            function=function,
            chars=len(content),
            api_style=self.api_style,
        )
        return content

    def _chat_local(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str:
        style = self.api_style
        if style == "ollama":
            return self._chat_ollama(messages, model=model, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        if style in {"openai", "openai-compatible"}:
            return self._chat_openai(messages, model=model, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        if re.search(r"/v\d+$", self.base_url):
            return self._chat_openai(messages, model=model, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        try:
            return self._chat_ollama(messages, model=model, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            lifecycle_debug.log("life.local_thinking.ollama_404_fallback_openai", base_url=self.base_url, model=model)
            return self._chat_openai(messages, model=model, temperature=temperature, max_tokens=max_tokens, timeout=timeout)

    def _chat_ollama(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        data = self._post_json(f"{self.base_url}/api/chat", payload, timeout=timeout)
        return str(data.get("message", {}).get("content") or "").strip()

    def _chat_openai(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str:
        base = self.base_url
        if not re.search(r"/v\d+$", base):
            base += "/v1"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = self._post_json(f"{base}/chat/completions", payload, timeout=timeout)
        return str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()

    def _post_json(self, url: str, payload: dict, *, timeout: int) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
