"""Inner narrative stream for self-expression.

The stream is intentionally plain text.  Runtime code may read, write, and
inject it, but must not parse it into rules or scores.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from kokoro import input_events

logger = logging.getLogger(__name__)

_CHARACTERS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "characters",
)


class InnerStream:
    """A character's current inner continuity, maintained by an LLM."""

    def __init__(self, character_id: str, character_data: dict | None = None):
        self.character_id = character_id
        self.character_data = character_data or {}
        self._path = os.path.join(_CHARACTERS_DIR, character_id, "inner_stream.txt")
        self.text: str = ""
        self._load()

    def get_context(self) -> str:
        if not self.text.strip():
            return ""
        return "【内在叙事流】\n" + self.text.strip()

    def evaluate(
        self,
        *,
        user_text: str,
        assistant_text: str,
        character_name: str,
        user_name: str,
        summary: str = "",
        recent_history: str = "",
        cognition_context: str = "",
        emotion_context: str = "",
        memory_context: str = "",
        scene_context: str = "",
    ) -> dict:
        """Rewrite the stream after a meaningful turn.

        This returns debug data for tests/tools.  Failures are non-fatal.
        """
        from kokoro import config as cfg
        from kokoro import prompts
        from kokoro import token_usage

        debug = {
            "system_prompt": "",
            "user_prompt": "",
            "raw_response": "",
            "before": self.text,
            "after": self.text,
            "saved": False,
            "error": "",
        }

        section = cfg.inner_stream_config()
        if not section.get("enabled", True):
            return debug

        system_prompt = prompts.format_prompt(
            "inner_stream.evaluate_system",
            name=character_name,
            user_name=user_name,
        )
        profile = _compact_profile(self.character_data)
        user_prompt = prompts.format_prompt(
            "inner_stream.evaluate_user",
            name=character_name,
            user_name=user_name,
            current=self.text.strip() or "（空）",
            profile=profile or "（无）",
            summary=summary or "（无）",
            recent_history=recent_history or "（无）",
            cognition_context=cognition_context or "（无）",
            emotion_context=emotion_context or "（无）",
            memory_context=memory_context or "（无）",
            scene_context=scene_context or "（无）",
            user_text=user_text or "（无）",
            assistant_text=assistant_text or "（无）",
        )
        debug["system_prompt"] = system_prompt
        debug["user_prompt"] = user_prompt

        model = str(section.get("model") or "").strip() or cfg.llm_model()
        url = cfg.llm_url()
        api_key = ""
        openai_compatible = False
        if cfg.is_deepseek_model(model):
            api_key = cfg.deepseek_api_key()
            url = cfg.deepseek_url()
            openai_compatible = True

        headers = {"Content-Type": "application/json"}
        max_tokens = int(section.get("max_tokens", 700) or 700)
        try:
            import urllib.request

            if openai_compatible:
                headers["Authorization"] = f"Bearer {api_key}"
                base_url = url.rstrip("/")
                if not re.search(r"/v\d+$", base_url):
                    base_url += "/v1"
                api_url = f"{base_url}/chat/completions"
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.5,
                    "max_tokens": max_tokens,
                }
            else:
                api_url = f"{url}/api/chat"
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.5, "num_predict": max_tokens},
                }

            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read())

            if openai_compatible:
                usage = result.get("usage", {})
                pt = int(usage.get("prompt_tokens", 0))
                ct = int(usage.get("completion_tokens", 0))
                if pt or ct:
                    token_usage.record(model, "inner_stream_evaluate", pt, ct)
                text = (
                    result.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
            else:
                pt = int(result.get("prompt_eval_count", 0))
                ct = int(result.get("eval_count", 0))
                if pt or ct:
                    token_usage.record(model, "inner_stream_evaluate", pt, ct)
                text = result.get("message", {}).get("content", "").strip()

            debug["raw_response"] = text
            cleaned = _clean_stream_text(text, max_chars=int(section.get("max_chars", 1200) or 1200))
            if cleaned and _looks_complete(cleaned):
                self.text = cleaned
                self._save()
                debug["after"] = self.text
                debug["saved"] = True
        except Exception as exc:
            debug["error"] = str(exc)
            logger.debug("inner stream evaluation failed: %s", exc)
        return debug

    def evaluate_events(
        self,
        *,
        events: list[input_events.InputEvent],
        character_name: str,
        user_name: str,
        summary: str = "",
        recent_history: str = "",
        cognition_context: str = "",
        emotion_context: str = "",
        memory_context: str = "",
        scene_context: str = "",
        activity_context: str = "",
        trigger_reason: str = "",
    ) -> dict:
        """Rewrite the stream from a batch of unified runtime events."""
        from kokoro import config as cfg
        from kokoro import prompts
        from kokoro import token_usage

        debug = {
            "system_prompt": "",
            "user_prompt": "",
            "raw_response": "",
            "before": self.text,
            "after": self.text,
            "saved": False,
            "error": "",
        }

        section = cfg.inner_stream_config()
        if not section.get("enabled", True) or not events:
            return debug

        system_prompt = prompts.format_prompt(
            "inner_stream.events_system",
            name=character_name,
            user_name=user_name,
        ) or _events_system_prompt(character_name, user_name)
        profile = _compact_profile(self.character_data)
        user_prompt = prompts.format_prompt(
            "inner_stream.events_user",
            name=character_name,
            user_name=user_name,
            current=self.text.strip() or "（空）",
            profile=profile or "（无）",
            summary=summary or "（无）",
            recent_history=recent_history or "（无）",
            cognition_context=cognition_context or "（无）",
            emotion_context=emotion_context or "（无）",
            memory_context=memory_context or "（无）",
            scene_context=scene_context or "（无）",
            activity_context=activity_context or "（无）",
            events=input_events.format_events_for_prompt(
                events,
                max_chars=int(section.get("event_prompt_max_chars", 3200) or 3200),
            ) or "（无）",
            trigger_reason=trigger_reason or "事件短窗口合并",
        ) or _events_user_prompt(
            name=character_name,
            user_name=user_name,
            current=self.text.strip() or "（空）",
            profile=profile or "（无）",
            summary=summary or "（无）",
            recent_history=recent_history or "（无）",
            cognition_context=cognition_context or "（无）",
            emotion_context=emotion_context or "（无）",
            memory_context=memory_context or "（无）",
            scene_context=scene_context or "（无）",
            activity_context=activity_context or "（无）",
            events=input_events.format_events_for_prompt(events),
            trigger_reason=trigger_reason or "事件短窗口合并",
        )
        debug["system_prompt"] = system_prompt
        debug["user_prompt"] = user_prompt

        model = str(section.get("model") or "").strip() or cfg.llm_model()
        url = cfg.llm_url()
        api_key = ""
        openai_compatible = False
        if cfg.is_deepseek_model(model):
            api_key = cfg.deepseek_api_key()
            url = cfg.deepseek_url()
            openai_compatible = True

        headers = {"Content-Type": "application/json"}
        max_tokens = int(section.get("max_tokens", 700) or 700)
        try:
            import urllib.request

            if openai_compatible:
                headers["Authorization"] = f"Bearer {api_key}"
                base_url = url.rstrip("/")
                if not re.search(r"/v\d+$", base_url):
                    base_url += "/v1"
                api_url = f"{base_url}/chat/completions"
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.5,
                    "max_tokens": max_tokens,
                }
            else:
                api_url = f"{url}/api/chat"
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.5, "num_predict": max_tokens},
                }

            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read())

            if openai_compatible:
                usage = result.get("usage", {})
                pt = int(usage.get("prompt_tokens", 0))
                ct = int(usage.get("completion_tokens", 0))
                if pt or ct:
                    token_usage.record(model, "inner_stream_events", pt, ct)
                text = (
                    result.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
            else:
                pt = int(result.get("prompt_eval_count", 0))
                ct = int(result.get("eval_count", 0))
                if pt or ct:
                    token_usage.record(model, "inner_stream_events", pt, ct)
                text = result.get("message", {}).get("content", "").strip()

            debug["raw_response"] = text
            cleaned = _clean_stream_text(text, max_chars=int(section.get("max_chars", 1200) or 1200))
            if cleaned and _looks_complete(cleaned):
                self.text = cleaned
                self._save()
                debug["after"] = self.text
                debug["saved"] = True
        except Exception as exc:
            debug["error"] = str(exc)
            logger.debug("inner stream event evaluation failed: %s", exc)
        return debug

    def _load(self) -> None:
        if not os.path.exists(self._path):
            self.text = ""
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self.text = _clean_stream_text(f.read(), max_chars=1600)
        except Exception as exc:
            logger.warning("failed to load inner stream: %s", exc)
            self.text = ""

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                f.write(self.text.strip() + "\n")
        except Exception as exc:
            logger.warning("failed to save inner stream: %s", exc)


def _clean_stream_text(text: str, *, max_chars: int) -> str:
    text = re.sub(r"```(?:text|markdown)?\s*\n?(.*?)```", r"\1", str(text), flags=re.DOTALL)
    text = text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:max(200, max_chars)].strip()


def _looks_complete(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 40:
        return False
    if stripped.endswith(("这", "那", "但", "而", "因为", "所以", "如果", "不是", "可以", "一个")):
        return False
    return True


def _compact_profile(data: dict) -> str:
    parts: list[str] = []
    for key in ("name", "description", "personality", "background", "relationship"):
        value = str(data.get(key, "") or "").strip()
        if value:
            parts.append(f"{key}: {value[:500]}")
    return "\n\n".join(parts)


class InnerStreamLoop:
    """Rhythm-driven inner narrative updater.

    Events wake the loop and can pull the next update closer, but the loop
    updates at its own cadence and reads the queued events only when due.
    """

    def __init__(
        self,
        *,
        stream: InnerStream,
        context_provider,
        event_delay_seconds: float = 2.0,
        idle_interval_seconds: float = 240.0,
        time_tick_interval_seconds: float = 900.0,
        max_batch: int = 16,
    ) -> None:
        self.stream = stream
        self.context_provider = context_provider
        self.event_delay_seconds = max(0.0, float(event_delay_seconds))
        self.idle_interval_seconds = max(10.0, float(idle_interval_seconds))
        self.time_tick_interval_seconds = max(0.0, float(time_tick_interval_seconds))
        self.max_batch = max(1, int(max_batch))
        self._events: list[input_events.InputEvent] = []
        self._lock = threading.Lock()
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        now = time.monotonic()
        self._next_due = now + self.idle_interval_seconds
        self._last_update = 0.0
        self._last_time_tick = now

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, *, flush: bool = True) -> None:
        if flush:
            self.flush()
        self._stop.set()
        self._wakeup.set()

    def submit(self, event: input_events.InputEvent) -> None:
        with self._lock:
            self._events.append(event)
            self._schedule_for_event_locked(event)
        self._wakeup.set()

    def flush(self) -> None:
        events = self._pop_events()
        if not events:
            events = [self._time_tick_event(reason="manual flush")]
        self._evaluate(events, trigger_reason="manual flush")

    def _run(self) -> None:
        while not self._stop.is_set():
            timeout = self._seconds_until_due()
            self._wakeup.wait(timeout=timeout)
            self._wakeup.clear()
            if self._stop.is_set():
                break
            if not self._is_due():
                continue
            events = self._pop_events(limit=self.max_batch)
            reason = "节奏更新"
            if not events and self._should_time_tick():
                events = [self._time_tick_event(reason="idle rhythm")]
                reason = "低频时间节奏"
            elif events:
                reason = "事件唤醒后的节奏更新"
            if events:
                self._evaluate(events, trigger_reason=reason)
            self._schedule_next_idle()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._events)

    def _pop_events(self, limit: int | None = None) -> list[input_events.InputEvent]:
        with self._lock:
            if not self._events:
                return []
            if limit is None or len(self._events) <= limit:
                events = self._events
                self._events = []
                return events
            events = self._events[:limit]
            self._events = self._events[limit:]
            if self._events:
                self._schedule_soon_locked()
                self._wakeup.set()
            return events

    def _evaluate(self, events: list[input_events.InputEvent], *, trigger_reason: str) -> None:
        try:
            context = self.context_provider() or {}
            self.stream.evaluate_events(events=events, trigger_reason=trigger_reason, **context)
            self._last_update = time.monotonic()
        except Exception as exc:
            logger.warning("inner stream loop failed: %s", exc)

    def _schedule_for_event_locked(self, event: input_events.InputEvent) -> None:
        now = time.monotonic()
        delay = self.event_delay_seconds
        if event.priority == "urgent":
            delay = min(delay, 0.5)
        elif event.priority == "high":
            delay = min(delay, 1.0)
        elif event.priority == "low":
            delay = max(delay, min(30.0, self.idle_interval_seconds))
        self._next_due = min(self._next_due, now + delay)

    def _schedule_soon_locked(self) -> None:
        self._next_due = min(self._next_due, time.monotonic() + self.event_delay_seconds)

    def _schedule_next_idle(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._next_due = now + self.idle_interval_seconds
            if self._events:
                self._schedule_soon_locked()

    def _seconds_until_due(self) -> float:
        with self._lock:
            due = self._next_due
        return max(0.1, min(60.0, due - time.monotonic()))

    def _is_due(self) -> bool:
        with self._lock:
            return time.monotonic() >= self._next_due

    def _should_time_tick(self) -> bool:
        if self.time_tick_interval_seconds <= 0:
            return False
        return time.monotonic() - self._last_time_tick >= self.time_tick_interval_seconds

    def _time_tick_event(self, *, reason: str) -> input_events.InputEvent:
        now = time.monotonic()
        elapsed = now - (self._last_update or self._last_time_tick)
        self._last_time_tick = now
        return input_events.build_time_tick_event(
            f"距离上次内在叙事流更新约 {elapsed:.0f} 秒，期间没有更强输入必须立刻处理。",
            metadata={"reason": reason, "elapsed_seconds": round(elapsed, 1)},
        )


def _events_system_prompt(name: str, user_name: str) -> str:
    return (
        f"你是{name}的内在叙事流整理器。你的任务不是替{name}写台词，也不是制定计划，"
        "而是根据持续进入的输入事件和自身行动事件，维护她当前仍在延续的心理状态。\n\n"
        "只维护自然语言文本。不要输出 JSON，不要打分，不要写程序规则。\n\n"
        "核心原则：\n"
        "- 所有人、网页、屏幕、消息和时间变化都只是输入源；没有任何输入天然是中心。\n"
        "- 说话、沉默、观察和等待都可能是有效行动。\n"
        "- 内在叙事流记录“我现在作为我还在想什么”，不是事实档案、计划表或长期人格。\n"
        "- 区分短期刺激、持续兴趣和长期认知。\n"
        "- 保留未完成感，但不要写成强制待办。\n\n"
        "输出必须稳定包含以下栏目：\n"
        "【当前心境】\n【注意力焦点】\n【外部输入态势】\n【未完成感】\n【行动倾向】\n【克制与边界】"
    )


def _events_user_prompt(**kwargs) -> str:
    return (
        f"请根据以下材料，重写{kwargs['name']}当前的内在叙事流。\n\n"
        f"【当前内在叙事流】\n{kwargs['current']}\n\n"
        f"【时间与运行状态】\n触发原因：{kwargs['trigger_reason']}\n\n"
        f"【角色资料】\n{kwargs['profile']}\n\n"
        f"【最近输入事件】\n{kwargs['events'] or '（无）'}\n\n"
        f"【最近自身行动】\n{kwargs['activity_context']}\n\n"
        f"【最近对话】\n{kwargs['recent_history']}\n\n"
        f"【对话摘要】\n{kwargs['summary']}\n\n"
        f"【当前环境摘要】\n{kwargs['scene_context']}\n\n"
        f"【相关长期记忆】\n{kwargs['memory_context']}\n\n"
        f"【认知与情绪】\n{kwargs['cognition_context']}\n\n{kwargs['emotion_context']}\n\n"
        "要求：\n"
        "- 写成第一人称或贴近第一人称的内在连续性，不要写第三方分析报告。\n"
        "- 不要复述所有事件，只吸收真正影响当前状态的内容。\n"
        "- 隐私事件只能作为弱信号，不得补写正文。\n"
        "- 行动倾向是自然倾向，不是命令或待办清单。\n"
        "- 严格使用六个栏目标题。\n\n"
        "更新后的内在叙事流："
    )
