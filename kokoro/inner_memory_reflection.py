"""Inner-stream guided memory reflection.

This module asks the LLM whether recent inputs, self actions, and the updated
inner stream have become a complete experience worth remembering. It stores
natural-language event descriptions; it does not create artificial indexes.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.request
from typing import Any

from kokoro import config as cfg
from kokoro import input_events
from kokoro import token_usage

logger = logging.getLogger(__name__)


class InnerMemoryReflection:
    def __init__(
        self,
        *,
        session,
        section: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        section = section or {}
        self.enabled = bool(section.get("enabled", True))
        self.model = str(section.get("model") or "").strip() or cfg.llm_model()
        self.max_tokens = int(section.get("max_tokens", 512) or 512)
        self._lock = threading.Lock()

    def consider(
        self,
        *,
        inner_stream: str,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str = "",
    ) -> None:
        if not self.enabled or not events or not str(inner_stream or "").strip():
            return
        if not self._lock.acquire(blocking=False):
            return
        thread = threading.Thread(
            target=self._run,
            kwargs={
                "inner_stream": inner_stream,
                "events": list(events),
                "context": dict(context or {}),
                "trigger_reason": trigger_reason,
            },
            daemon=True,
        )
        thread.start()

    def _run(
        self,
        *,
        inner_stream: str,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str,
    ) -> None:
        try:
            decision = self._decide(
                inner_stream=inner_stream,
                events=events,
                context=context,
                trigger_reason=trigger_reason,
            )
            if not decision.get("remember"):
                return
            desc = str(decision.get("event") or "").strip()
            if not desc:
                return
            tags = decision.get("tags")
            if not isinstance(tags, list):
                tags = ["inner_stream"]
            tags = [str(t).strip() for t in tags if str(t).strip()][:6]
            memory_events = getattr(self.session, "memory_events", None)
            if memory_events is not None and hasattr(memory_events, "add_direct_event"):
                memory_events.add_direct_event(desc, tags=tags)
            else:
                backend = getattr(self.session, "memory_backend", None)
                if backend is not None and hasattr(backend, "store"):
                    backend.store(desc, "inner_stream_reflection", user_id=self.session.character_id)
            record = getattr(self.session, "record_self_action", None)
            if callable(record):
                record(
                    f"我把刚才的经历整理成了一条记忆：{desc}",
                    source="inner_memory",
                    action="remember",
                    metadata={"tags": tags},
                )
        except Exception as exc:
            logger.debug("inner memory reflection failed: %s", exc)
        finally:
            self._lock.release()

    def _decide(
        self,
        *,
        inner_stream: str,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str,
    ) -> dict[str, Any]:
        prompt = self._build_prompt(
            inner_stream=inner_stream,
            events=events,
            context=context,
            trigger_reason=trigger_reason,
        )
        raw = self._call_model(prompt)
        return _extract_json_object(raw) or {}

    def _build_prompt(
        self,
        *,
        inner_stream: str,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str,
    ) -> str:
        name = self.session.character_name
        return (
            f"你正在帮助{name}从自己的内在叙事流里判断：刚才是否形成了一件值得长期记住的经历。\n"
            "记忆应该是一整件自然语言事件，不是人工索引，不要拆成 group_id/user_id 字段。"
            "如果涉及 QQ 群、人物、搜索、发言、沉默或理解变化，就在事件描述里自然写清楚群名/位置、人物、发生了什么、"
            f"{name}做了什么、她如何理解。\n"
            "如果只是短暂噪音、无意义闲聊、重复状态，就 remember=false。\n\n"
            "只输出 JSON：\n"
            '{"remember": boolean, "event": "一整件值得记住的经历", "tags": ["标签"]}\n\n'
            f"触发原因：{trigger_reason}\n\n"
            f"当前内在叙事流：\n{inner_stream}\n\n"
            f"最近输入/自身行动事件：\n{input_events.format_events_for_prompt(events, max_chars=3000) or '无'}\n\n"
            f"最近对话：\n{context.get('recent_history') or '无'}\n\n"
            f"已有摘要：\n{context.get('summary') or '无'}\n\n"
            f"相关记忆：\n{context.get('memory_context') or '无'}\n\n"
            "JSON："
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
                "temperature": 0.2,
                "max_tokens": self.max_tokens,
                "response_format": {"type": "json_object"},
            }
        else:
            api_url = f"{cfg.llm_url().rstrip('/')}/api/chat"
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": self.max_tokens},
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
                token_usage.record(model, "inner_memory_reflection", pt, ct)
            return str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        pt = int(data.get("prompt_eval_count", 0) or 0)
        ct = int(data.get("eval_count", 0) or 0)
        if pt or ct:
            token_usage.record(model, "inner_memory_reflection", pt, ct)
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
