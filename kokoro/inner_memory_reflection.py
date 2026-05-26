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
import time
from typing import Any

from kokoro import config as cfg
from kokoro import deepseek_api
from kokoro import input_events

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
        self.consider_interval_seconds = max(0.0, float(section.get("consider_interval_seconds", 30.0) or 30.0))
        self.min_events = max(1, int(section.get("min_events", 2) or 2))
        self._last_consider_at = 0.0
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
        meaningful_events = [event for event in events if event.type != "time_tick"]
        if len(meaningful_events) < self.min_events:
            return
        now = time.monotonic()
        if self.consider_interval_seconds > 0 and now - self._last_consider_at < self.consider_interval_seconds:
            return
        self._last_consider_at = now
        if not self._lock.acquire(blocking=False):
            return
        thread = threading.Thread(
            target=self._run,
            kwargs={
                "inner_stream": inner_stream,
                "events": meaningful_events,
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
            self._run_unlocked(
                inner_stream=inner_stream,
                events=events,
                context=context,
                trigger_reason=trigger_reason,
            )
        except Exception as exc:
            logger.debug("inner memory reflection failed: %s", exc)
        finally:
            self._lock.release()

    def _run_unlocked(
        self,
        *,
        inner_stream: str,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str,
    ) -> None:
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

    def run_sync(
        self,
        *,
        inner_stream: str,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str = "",
    ) -> None:
        if not self.enabled or not events or not str(inner_stream or "").strip():
            return
        meaningful_events = [event for event in events if event.type != "time_tick"]
        if not meaningful_events:
            return
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._run_unlocked(
                inner_stream=inner_stream,
                events=meaningful_events,
                context=dict(context or {}),
                trigger_reason=trigger_reason,
            )
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
        prompt = self._build_user_prompt(
            inner_stream=inner_stream,
            events=events,
            context=context,
            trigger_reason=trigger_reason,
        )
        system = self._build_system_prompt()
        raw = self._call_model(system, prompt)
        return _extract_json_object(raw) or {}

    def _build_system_prompt(self) -> str:
        name = self.session.character_name
        return (
            f"你是角色的内在记忆反思助手。只输出JSON。\n\n"
            f"你正在帮助{name}从自己的内在叙事流里判断：刚才是否形成了一件值得长期记住的经历。\n"
            "记忆应该是一整件自然语言事件，不是人工索引，不要拆成 group_id/user_id 字段。"
            "如果涉及 QQ 群、人物、搜索、发言、沉默或理解变化，就在事件描述里自然写清楚群名/位置、人物、发生了什么、"
            f"{name}做了什么、她如何理解。\n"
            "如果只是短暂噪音、无意义闲聊、重复状态，就 remember=false。\n\n"
            "只输出 JSON 格式：\n"
            '{"remember": boolean, "event": "一整件值得记住的经历", "tags": ["标签"]}\n\n'
            "如果最近有人明确指出某个线索是 bug、幻觉、误会或不要再管，"
            "不要把那个错误线索当作事实写入记忆；只能记住\"她被纠正并撤销了这个判断\"。\n\n"
            "请根据用户消息中提供的具体数据判断："
        )

    def _build_user_prompt(
        self,
        *,
        inner_stream: str,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str,
    ) -> str:
        return (
            f"【触发原因】\n{trigger_reason}\n\n"
            f"【当前内在叙事流】\n{inner_stream}\n\n"
            f"【最近输入/自身行动事件】\n{input_events.format_events_for_prompt(events, max_chars=3000) or '无'}\n\n"
            f"【最近对话】\n{context.get('recent_history') or '无'}\n\n"
            f"【已有摘要】\n{context.get('summary') or '无'}\n\n"
            f"【相关记忆】\n{context.get('memory_context') or '无'}\n\n"
            "JSON："
        )

    def _call_model(self, system_prompt: str, user_prompt: str) -> str:
        return deepseek_api.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=self.model,
            temperature=0.2,
            max_tokens=self.max_tokens,
            json_mode=True,
            function="inner_memory_reflection",
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
