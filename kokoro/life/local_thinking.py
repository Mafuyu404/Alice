"""Local/high-frequency LLM calls for life runtime prompt work."""

from __future__ import annotations

import json
import queue
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from kokoro.core import config as cfg
from kokoro.core import deepseek_api
from kokoro.core import lifecycle_debug


@dataclass(order=True)
class _QueuedCall:
    priority: int
    sequence: int
    messages: list[dict] = field(compare=False)
    options: dict[str, Any] = field(compare=False)
    done: threading.Event = field(default_factory=threading.Event, compare=False)
    content: str = field(default="", compare=False)
    error: BaseException | None = field(default=None, compare=False)
    cancelled: bool = field(default=False, compare=False)


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
        self.queue_enabled = bool(section.get("priority_queue", True))
        self._queue: queue.PriorityQueue[_QueuedCall] = queue.PriorityQueue()
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._worker_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._pending_lock = threading.Lock()
        self._pending_by_key: dict[str, _QueuedCall] = {}

    def chat(self, messages: list[dict], options: dict[str, Any] | None = None) -> str:
        if not self.enabled:
            return ""
        options = dict(options or {})
        if self.queue_enabled and not bool(options.get("bypass_priority_queue", False)):
            return self._chat_queued(messages, options)
        return self._chat_now(messages, options)

    def _chat_queued(self, messages: list[dict], options: dict[str, Any]) -> str:
        self._ensure_worker()
        with self._sequence_lock:
            self._sequence += 1
            sequence = self._sequence
        function = str(options.get("function") or "life_local_thinking")
        call = _QueuedCall(
            priority=_priority_for_function(function, options),
            sequence=sequence,
            messages=messages,
            options=options,
        )
        coalesce_key = _coalesce_key_for_function(function, options)
        if coalesce_key:
            with self._pending_lock:
                previous = self._pending_by_key.get(coalesce_key)
                if previous is not None and not previous.done.is_set():
                    previous.cancelled = True
                    previous.done.set()
                    lifecycle_debug.log(
                        "life.local_thinking.coalesced",
                        function=function,
                        coalesce_key=coalesce_key,
                        cancelled_sequence=previous.sequence,
                        sequence=sequence,
                    )
                self._pending_by_key[coalesce_key] = call
        lifecycle_debug.log(
            "life.local_thinking.queued",
            function=function,
            priority=call.priority,
            sequence=sequence,
            queue_size=self._queue.qsize(),
        )
        self._queue.put(call)
        call.done.wait()
        if call.error is not None:
            raise call.error
        return call.content

    def _ensure_worker(self) -> None:
        with self._worker_lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run_worker,
                daemon=True,
                name=f"life-local-thinking-{self.model}",
            )
            self._worker.start()

    def _run_worker(self) -> None:
        while True:
            call = self._queue.get()
            try:
                if not call.cancelled:
                    call.content = self._chat_now(call.messages, call.options)
            except BaseException as exc:
                call.error = exc
            finally:
                coalesce_key = _coalesce_key_for_function(str(call.options.get("function") or ""), call.options)
                if coalesce_key:
                    with self._pending_lock:
                        if self._pending_by_key.get(coalesce_key) is call:
                            self._pending_by_key.pop(coalesce_key, None)
                call.done.set()
                self._queue.task_done()

    def _chat_now(self, messages: list[dict], options: dict[str, Any]) -> str:
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


def _priority_for_function(function: str, options: dict[str, Any]) -> int:
    if "priority" in options:
        try:
            return int(options["priority"])
        except Exception:
            pass
    name = str(function or "").strip()
    if name == "life_tick":
        return 0
    if name in {"life_tick_json_repair", "life_inner_stream_patch_fallback"}:
        return 1
    if name == "life_context_compact":
        return 2
    if name == "memory_experience_workspace":
        return 6
    if name == "memory_lifecycle":
        return 7
    return 4


def _coalesce_key_for_function(function: str, options: dict[str, Any]) -> str:
    if bool(options.get("no_coalesce", False)):
        return ""
    name = str(function or "").strip()
    if name in {"memory_experience_workspace", "memory_lifecycle"}:
        return name
    return ""
