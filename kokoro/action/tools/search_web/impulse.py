"""LLM-guided autonomous web search impulse for the inner stream."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from kokoro.core import config as cfg
from kokoro.core import deepseek_api
from kokoro.core import prompts
from kokoro.action.tools.search_web.client import WebSearchClient, format_search_result

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
                result = self._retry_low_quality_result(decision.query, result)
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

    def _retry_low_quality_result(self, query: str, result: dict[str, Any]) -> dict[str, Any]:
        if not _looks_like_low_quality_result(query, result):
            return result
        refined = _refined_query(query)
        if not refined or refined == query:
            return result
        try:
            logger.info("inner stream web search retry: query=%r refined=%r", query, refined)
            retried = self.client.search(refined, limit=max(self.max_results, 8))
        except Exception as exc:
            logger.info("inner stream web search retry failed: query=%r error=%s", refined, exc)
            return result
        if not _looks_like_low_quality_result(query, retried):
            return retried
        return result

    def _decide(self, *, inner_stream: str, context: dict[str, Any]) -> SearchImpulseDecision:
        prompt = self._build_prompt(inner_stream=inner_stream, context=context)
        raw = self._call_model(prompt)
        data = _extract_json_object(raw)
        if not data:
            return SearchImpulseDecision()
        query = str(data.get("query") or "").strip()
        search = bool(data.get("search", False)) and bool(query)
        if search and _looks_like_private_memory_lookup(query, str(data.get("reason") or ""), inner_stream):
            logger.info(
                "inner stream web search looks like private memory lookup; honoring LLM decision: query=%r",
                query,
            )
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
        return deepseek_api.chat(
            [
                {"role": "system", "content": prompts.get("web_search_impulse.decision_system", "")},
                {"role": "user", "content": prompt},
            ],
            model=self.model,
            temperature=0.3,
            max_tokens=256,
            json_mode=True,
            function="inner_stream_search_impulse",
        )["content"]


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


def _looks_like_low_quality_result(query: str, result: dict[str, Any]) -> bool:
    items = _extract_search_items(result)
    if not items:
        return True
    query_text = str(query or "").strip()
    compact_query = re.sub(r"\s+", "", query_text).lower()
    tokens = [token.lower() for token in re.split(r"\s+", query_text) if len(token) >= 2]
    if not compact_query and not tokens:
        return False
    for item in items:
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("title", "name", "url", "link", "href", "source", "snippet", "content", "description", "summary")
        ).lower()
        compact_haystack = re.sub(r"\s+", "", haystack)
        if compact_query and compact_query in compact_haystack:
            return False
        if tokens and all(token in haystack for token in tokens[:4]):
            return False
    return True


def _refined_query(query: str) -> str:
    value = str(query or "").strip()
    if not value:
        return ""
    return value


def _extract_search_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if not isinstance(value, dict):
        return []
    if isinstance(value.get("data"), dict):
        nested = _extract_search_items(value["data"])
        if nested:
            return nested
    for key in ("results", "items", "data", "organic", "webPages"):
        candidate = value.get(key)
        if isinstance(candidate, list):
            return [x for x in candidate if isinstance(x, dict)]
        if isinstance(candidate, dict):
            nested = _extract_search_items(candidate)
            if nested:
                return nested
    return []


def _looks_like_private_memory_lookup(query: str, reason: str = "", inner_stream: str = "") -> bool:
    text = " ".join([str(query or ""), str(reason or ""), str(inner_stream or "")[-1200:]])
    direct_private_markers = (
        "\u7fa4\u804a\u8bb0\u5f55",
        "\u7fa4\u8bb0\u5f55",
        "\u804a\u5929\u8bb0\u5f55",
        "\u7ffb\u8bb0\u5f55",
        "\u7ffb\u7fa4",
        "\u79c1\u4e0b\u7ffb",
        "\u8bb0\u5fc6\u788e\u7247",
        "\u8bb0\u5f97",
        "\u662f\u8c01",
        "\u5370\u8c61",
        "\u548c\u6211\u6709\u4ec0\u4e48\u4e92\u52a8",
        "\u5bf9\u67d0\u4eba",
        "\u7fa4\u53cb",
    )
    direct_public_markers = (
        "\u5b98\u7f51",
        "\u767e\u79d1",
        "\u65b0\u95fb",
        "\u4ef7\u683c",
        "\u6587\u6863",
        "\u8bba\u6587",
        "\u53d1\u5e03",
        "\u7248\u672c",
        "API",
        "GitHub",
    )
    if any(marker in text for marker in direct_private_markers):
        return not any(marker in text for marker in direct_public_markers)
    private_markers = (
        "群聊记录",
        "群记录",
        "聊天记录",
        "翻记录",
        "翻群",
        "私下翻",
        "记忆碎片",
        "记得",
        "是谁",
        "印象",
        "和我有什么互动",
    )
    if not any(marker in text for marker in private_markers):
        return False
    public_markers = (
        "官网",
        "百科",
        "新闻",
        "价格",
        "文档",
        "论文",
        "发布",
        "版本",
        "API",
        "GitHub",
    )
    return not any(marker in text for marker in public_markers)
