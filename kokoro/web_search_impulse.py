"""LLM-guided autonomous web search impulse for the inner stream."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from kokoro import config as cfg
from kokoro import prompts
from kokoro import token_usage
from kokoro.web_search_client import WebSearchClient, format_search_result

logger = logging.getLogger(__name__)


@dataclass
class SearchImpulseDecision:
    search: bool = False
    query: str = ""
    reason: str = ""
    expected_use: str = ""


class InnerStreamSearchImpulse:
    """Let the LLM decide whether the current inner stream wants to search."""

    def __init__(
        self,
        *,
        character_name: str,
        user_name: str,
        event_callback: Callable[[str, str, dict[str, Any]], None],
        section: dict[str, Any] | None = None,
    ) -> None:
        section = section or {}
        self.character_name = character_name
        self.user_name = user_name
        self.event_callback = event_callback
        self.model = str(section.get("model") or "").strip() or cfg.llm_model()
        self.client = WebSearchClient(
            base_url=str(section.get("base_url") or "http://127.0.0.1:3000"),
            timeout=float(section.get("timeout", 45.0) or 45.0),
        )
        self.max_results = int(section.get("max_results", 5) or 5)
        self.max_event_chars = int(section.get("max_event_chars", 6000) or 6000)
        self.consider_interval_seconds = max(0.0, float(section.get("consider_interval_seconds", 5.0) or 5.0))
        self._last_consider_at = 0.0
        self._running_lock = threading.Lock()

    def consider(self, *, inner_stream: str, context: dict[str, Any] | None = None) -> None:
        """Start one background search consideration if no previous one is running."""
        if not str(inner_stream or "").strip():
            return
        now = time.monotonic()
        if self.consider_interval_seconds > 0 and now - self._last_consider_at < self.consider_interval_seconds:
            return
        self._last_consider_at = now
        if not self._running_lock.acquire(blocking=False):
            return
        thread = threading.Thread(
            target=self._run,
            kwargs={"inner_stream": inner_stream, "context": context or {}},
            daemon=True,
        )
        thread.start()

    def _run(self, *, inner_stream: str, context: dict[str, Any]) -> None:
        try:
            decision = self._decide(inner_stream=inner_stream, context=context)
            if not decision.search or not decision.query:
                logger.info("inner stream web search skipped: %s", decision.reason or "no search impulse")
                return
            logger.info(
                "inner stream web search intent: query=%r reason=%s",
                decision.query,
                decision.reason or "",
            )
            self.event_callback(
                f"我想确认一下：{decision.query}\n原因：{decision.reason or '内在叙事流产生了搜索倾向'}",
                "web_search",
                {
                    "action": "web_search_intent",
                    "query": decision.query,
                    "reason": decision.reason,
                    "expected_use": decision.expected_use,
                },
            )
            try:
                logger.info("inner stream web search request: query=%r limit=%s", decision.query, self.max_results)
                result = self.client.search(decision.query, limit=self.max_results)
                content = format_search_result(
                    decision.query,
                    result,
                    max_chars=self.max_event_chars,
                )
                result_count = len(result.get("results") or result.get("items") or []) if isinstance(result, dict) else 0
                logger.info(
                    "inner stream web search result: query=%r results=%s",
                    decision.query,
                    result_count,
                )
                self.event_callback(
                    content,
                    "web_search",
                    {
                        "action": "web_search_result",
                        "query": decision.query,
                        "reason": decision.reason,
                        "expected_use": decision.expected_use,
                    },
                )
            except Exception as exc:
                logger.warning(
                    "inner stream web search error: query=%r error=%s: %s",
                    decision.query,
                    type(exc).__name__,
                    exc,
                )
                self.event_callback(
                    f"我尝试搜索：{decision.query}\n但搜索失败了：{type(exc).__name__}: {exc}",
                    "web_search",
                    {
                        "action": "web_search_error",
                        "query": decision.query,
                        "reason": decision.reason,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        except Exception as exc:
            logger.debug("web search impulse failed: %s", exc)
        finally:
            self._running_lock.release()

    def _decide(self, *, inner_stream: str, context: dict[str, Any]) -> SearchImpulseDecision:
        prompt = self._build_prompt(inner_stream=inner_stream, context=context)
        raw = self._call_model(prompt)
        data = _extract_json_object(raw)
        if not data:
            return SearchImpulseDecision()
        query = str(data.get("query") or "").strip()
        search = bool(data.get("search", False)) and bool(query)
        return SearchImpulseDecision(
            search=search,
            query=query,
            reason=str(data.get("reason") or "").strip(),
            expected_use=str(data.get("expected_use") or "").strip(),
        )

    def _build_prompt(self, *, inner_stream: str, context: dict[str, Any]) -> str:
        return prompts.format_prompt(
            "web_search_impulse.decision_user",
            character_name=self.character_name,
            user_name=self.user_name,
            inner_stream=inner_stream,
            recent_history=context.get("recent_history") or "无",
            summary=context.get("summary") or "无",
            scene_context=context.get("scene_context") or "无",
            memory_context=context.get("memory_context") or "无",
        )

    def _call_model(self, prompt: str) -> str:
        model = self.model
        headers = {"Content-Type": "application/json"}
        if cfg.is_deepseek_model(model):
            base_url = cfg.deepseek_url().rstrip("/")
            if not re.search(r"/v\d+$", base_url):
                base_url += "/v1"
            api_url = f"{base_url}/chat/completions"
            key = cfg.deepseek_api_key()
            if key:
                headers["Authorization"] = f"Bearer {key}"
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 256,
                "response_format": {"type": "json_object"},
            }
        else:
            api_url = f"{cfg.llm_url().rstrip('/')}/api/chat"
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 256},
            }
        req = urllib.request.Request(
            api_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())
        if cfg.is_deepseek_model(model):
            usage = data.get("usage") or {}
            pt = int(usage.get("prompt_tokens", 0) or 0)
            ct = int(usage.get("completion_tokens", 0) or 0)
            if pt or ct:
                token_usage.record(model, "inner_stream_search_impulse", pt, ct)
            return str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        usage = {
            "prompt_tokens": int(data.get("prompt_eval_count", 0) or 0),
            "completion_tokens": int(data.get("eval_count", 0) or 0),
        }
        if usage["prompt_tokens"] or usage["completion_tokens"]:
            token_usage.record(model, "inner_stream_search_impulse", usage["prompt_tokens"], usage["completion_tokens"])
        return str(data.get("message", {}).get("content") or "").strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = str(text or "").strip()
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
