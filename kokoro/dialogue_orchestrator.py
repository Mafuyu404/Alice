"""LLM-driven dialogue turn orchestration.

This module centralizes the decision of whether a character should speak now.
It intentionally avoids persona-specific if/else rules: the planner sees the
character profile and decides how that personality affects turn-taking.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

import requests

from kokoro import config as cfg
from kokoro import edge_cache
from kokoro import prompts
from kokoro import screen_interest
from kokoro import token_usage

logger = logging.getLogger(__name__)


SUPPORTED_ACTIONS = {
    "silence",
    "backchannel",
    "speak",
    "schedule",
    "observe",
    "cancel_plan",
}


@dataclass
class DialogueEvent:
    type: str
    text: str = ""
    source: str = "user"
    extra_context: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class DialogueDecision:
    action: str = "speak"
    delay_seconds: float = 0.0
    intent: str = ""
    topic: str = ""
    utterance_mode: str = "normal"
    context_use: str = "none"
    memory_policy: str = "normal"
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "DialogueDecision":
        action = str(data.get("action", "speak")).strip().lower()
        if action not in SUPPORTED_ACTIONS:
            action = "speak"
        try:
            delay = float(data.get("delay_seconds", 0) or 0)
        except (TypeError, ValueError):
            delay = 0.0
        return cls(
            action=action,
            delay_seconds=max(0.0, delay),
            intent=str(data.get("intent", "") or "").strip(),
            topic=str(data.get("topic", "") or "").strip(),
            utterance_mode=str(data.get("utterance_mode", "normal") or "normal").strip(),
            context_use=_normalize_context_use(str(data.get("context_use", "none") or "none")),
            memory_policy=str(data.get("memory_policy", "normal") or "normal").strip(),
            notes=str(data.get("notes", "") or "").strip(),
        )


@dataclass
class DialoguePlan:
    id: str
    due_at: float
    decision: DialogueDecision
    created_from: str = ""

    def to_prompt_line(self, now: float) -> str:
        remaining = max(0.0, self.due_at - now)
        topic = self.decision.topic or "无话题"
        intent = self.decision.intent or "无意图"
        return f"- {self.id}: {remaining:.0f}秒后到期，话题={topic}，意图={intent}"


class DialogueOrchestrator:
    def __init__(
        self,
        *,
        config: dict,
        session,
        model: str,
        memory_backend=None,
    ) -> None:
        section = config.get("dialogue", {})
        if not isinstance(section, dict):
            section = {}
        impulse_section = config.get("impulse", {})
        if not isinstance(impulse_section, dict):
            impulse_section = {}

        self.enabled = bool(section.get("enabled", True))
        self.planning_model = str(
            section.get("planning_model")
            or impulse_section.get("planning_model")
            or cfg.llm_model()
        )
        self.log_decisions = bool(section.get("log_decisions", True))
        self.max_recent_messages = max(2, int(section.get("max_recent_messages", 10)))
        self.max_character_prompt_chars = max(0, int(section.get("max_character_prompt_chars", 900)))
        self.max_profile_field_chars = max(80, int(section.get("max_profile_field_chars", 420)))
        self.max_delay_seconds = max(1.0, float(section.get("max_delay_seconds", 120.0)))
        self.screen_context_max_chars = max(200, int(section.get("screen_context_max_chars", 1200)))
        self.page_context_max_chars = max(500, int(section.get("page_context_max_chars", 2500)))
        self.idle_context_interval_seconds = max(5.0, float(section.get("idle_context_interval_seconds", 30.0)))
        self.context_idle_min_score = max(0.0, float(section.get("context_idle_min_score", 70.0)))
        self.edge_cache_config = edge_cache.config_from_dict(config)
        self.session = session
        self.model = model
        self.memory_backend = memory_backend
        self._plans: list[DialoguePlan] = []
        self._lock = threading.Lock()

    def decide(self, event: DialogueEvent) -> DialogueDecision:
        if not self.enabled:
            return DialogueDecision(action="speak", intent="兼容旧流程，直接回应")

        system_prompt = prompts.get("dialogue_orchestrator.planner_system", "")
        user_prompt = self._build_planner_user_prompt(event)
        try:
            raw = self._call_planner(system_prompt, user_prompt)
            data = _extract_json_object(raw)
            if not data:
                raise ValueError("planner did not return JSON object")
            decision = DialogueDecision.from_dict(data)
        except Exception as exc:
            logger.warning("dialogue planner failed: %s", exc)
            decision = DialogueDecision(
                action="backchannel",
                intent="规划失败时先短回应",
                utterance_mode="short",
                notes=f"planner fallback: {type(exc).__name__}",
            )

        if decision.action == "schedule":
            decision.delay_seconds = min(decision.delay_seconds, self.max_delay_seconds)

        if self.log_decisions:
            topic = f" topic={decision.topic}" if decision.topic else ""
            print(
                f"\n  [dialogue] action={decision.action} "
                f"delay={decision.delay_seconds:.0f}s mode={decision.utterance_mode} "
                f"context={decision.context_use}{topic}"
            )
            if decision.notes:
                print(f"  [dialogue] notes={decision.notes[:180]}")
        return decision

    def add_plan(self, decision: DialogueDecision, created_from: str = "") -> DialoguePlan:
        plan = DialoguePlan(
            id=str(uuid.uuid4())[:8],
            due_at=time.monotonic() + min(decision.delay_seconds, self.max_delay_seconds),
            decision=decision,
            created_from=created_from,
        )
        with self._lock:
            self._plans.append(plan)
            self._plans.sort(key=lambda p: p.due_at)
        return plan

    def cancel_plans(self) -> None:
        with self._lock:
            self._plans.clear()

    def pop_due_plan(self) -> DialoguePlan | None:
        now = time.monotonic()
        with self._lock:
            if not self._plans or self._plans[0].due_at > now:
                return None
            return self._plans.pop(0)

    def record_user_observation(self, text: str, decision: DialogueDecision) -> None:
        """Record heard-but-not-answered user text in short-term history."""
        if not text:
            return
        marker = (
            prompts.format_prompt(
                "dialogue_orchestrator.observation_marker",
                action=decision.action,
                name=self.session.character_name,
            )
            or f"【对话调度：{decision.action}】{self.session.character_name}听见了这句话，但没有立刻完整回应。"
        )
        with self.session._summarize_lock:
            self.session.history.append({"role": "user", "content": text})
            self.session.history.append({"role": "system", "content": marker})
            if len(self.session.history) > self.session.max_history * 2:
                self.session.history[:] = self.session.history[-self.session.max_history * 2:]
        try:
            self.session.cognition.refresh_cache(text, "")
        except Exception:
            pass

    def generator_context(self, decision: DialogueDecision) -> str:
        mode = decision.utterance_mode or decision.action
        if decision.action == "backchannel":
            action_instruction = prompts.get("dialogue_orchestrator.generator_backchannel_instruction", "")
        else:
            action_instruction = prompts.format_prompt(
                "dialogue_orchestrator.generator_speak_instruction",
                name=self.session.character_name,
            )
        return prompts.format_prompt(
            "dialogue_orchestrator.generator_context",
            name=self.session.character_name,
            user_name=self.session.user_name,
            action=decision.action,
            mode=mode,
            intent=decision.intent or "无",
            topic=decision.topic or "无",
            context_use=decision.context_use,
            action_instruction=action_instruction,
        )

    def build_reply_messages(
        self,
        *,
        user_text: str,
        decision: DialogueDecision,
        extra_context: str | None = None,
        max_history_messages: int | None = None,
    ) -> list[dict]:
        """Build a narrow prompt for the utterance generator.

        The normal ChatSession prompt is intentionally broad: memory, cognition,
        emotion, examples, and scene guidance are all useful in ordinary chat.
        For dialogue-orchestrated turns, that breadth can become topic leakage.
        This prompt keeps identity and style, then focuses on the current
        utterance and recent dialogue.
        """
        recent_count = max_history_messages or self.max_recent_messages
        character_prompt = _compact_character_prompt(
            self.session.character_data,
            self.session.user_name,
            self.session.character_name,
        )
        boundary = self.generator_context(decision)
        if _looks_like_system_design_topic(user_text, decision):
            boundary += "\n" + prompts.format_prompt(
                "dialogue_orchestrator.system_design_boundary",
                user_name=self.session.user_name,
            )
        messages: list[dict] = [
            {"role": "system", "content": character_prompt},
            {"role": "system", "content": boundary},
        ]
        cache_context = self.context_for_decision(decision)
        if cache_context:
            messages.append({"role": "system", "content": cache_context})
        if extra_context:
            messages.append({"role": "system", "content": extra_context})
        messages.extend(_filter_prompt_history(self.session.history[-recent_count:]))
        messages.append({"role": "user", "content": user_text})
        return messages

    def start_plan_executor(
        self,
        *,
        execute_fn: Callable[[DialogueDecision], None],
        cancel_event: threading.Event,
        poll_seconds: float = 0.25,
    ) -> threading.Thread:
        def _run() -> None:
            while not cancel_event.is_set():
                plan = self.pop_due_plan()
                if plan is None:
                    time.sleep(poll_seconds)
                    continue
                execute_fn(plan.decision)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return thread

    def build_context_event(self, *, reason: str = "idle_context") -> DialogueEvent:
        return DialogueEvent(
            type="context_cache",
            source=reason,
            extra_context=self._cache_overview_for_planner(),
        )

    def context_for_decision(self, decision: DialogueDecision) -> str:
        parts: list[str] = []
        if decision.context_use in ("screen", "both"):
            screen = self._screen_context_for_generator()
            if screen:
                parts.append(prompts.format_prompt("dialogue_orchestrator.screen_cache_context", screen=screen))
        if decision.context_use in ("page", "both"):
            page = self._page_context_for_generator()
            if page:
                parts.append(prompts.format_prompt("dialogue_orchestrator.page_cache_context", page=page))
        return "\n\n".join(parts)

    def _build_planner_user_prompt(self, event: DialogueEvent) -> str:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        character = self.session.character_data
        profile_parts = []
        for key in ("name", "description", "personality", "background", "relationship", "example_dialogue"):
            value = str(character.get(key, "") or "").strip()
            if value:
                profile_parts.append(f"{key}: {value[:self.max_profile_field_chars]}")
        profile = "\n\n".join(profile_parts) or "无角色资料"

        recent = _format_history(
            self.session.history[-self.max_recent_messages:],
            user_name=getattr(self.session, "user_name", "对方"),
            character_name=self.session.character_name,
        )
        summary = self.session.summary or "无"
        cognition = _safe_context(getattr(self.session, "cognition", None))
        emotion = _safe_context(getattr(self.session, "emotion", None))
        plans = self._plans_for_prompt()

        return prompts.format_prompt(
            "dialogue_orchestrator.planner_user",
            user_name=self.session.user_name,
            name=self.session.character_name,
            timestamp=now,
            event_type=event.type,
            speaker=event.source,
            event_text=event.text or "空",
            extra_context=event.extra_context or "无",
            profile=profile,
            system_prompt=self.session.system_prompt[:self.max_character_prompt_chars] if self.max_character_prompt_chars else "已省略",
            summary=summary,
            recent_history=recent or "无",
            cognition_context=cognition or "无",
            emotion_context=emotion or "无",
            pending_plans=plans or "无",
        )

    def _cache_overview_for_planner(self) -> str:
        parts: list[str] = []
        try:
            result, timestamp = screen_interest.get_cache().get()
        except Exception:
            result, timestamp = None, 0.0
        if result and result.content and result.score >= self.context_idle_min_score:
            age = max(0.0, time.time() - timestamp) if timestamp else 0.0
            parts.append(
                prompts.format_prompt(
                    "dialogue_orchestrator.screen_cache_candidate",
                    score=result.score,
                    age=age,
                    private=result.private,
                    content=result.content[:self.screen_context_max_chars],
                )
            )

        page = self._page_context_for_generator()
        if page:
            parts.append(
                prompts.format_prompt(
                    "dialogue_orchestrator.page_cache_candidate",
                    page=page[:self.page_context_max_chars],
                )
            )
        if not parts:
            return ""
        return "\n\n".join(parts)

    def _screen_context_for_generator(self) -> str:
        try:
            result, timestamp = screen_interest.get_cache().get()
        except Exception:
            return ""
        if not result or result.private or not result.content:
            return ""
        age = max(0.0, time.time() - timestamp) if timestamp else 0.0
        return f"score={result.score:.1f}, age={age:.0f}s\n{result.content[:self.screen_context_max_chars]}"

    def _page_context_for_generator(self) -> str:
        if not self.edge_cache_config.enabled:
            return ""
        try:
            return edge_cache.format_for_prompt(
                self.edge_cache_config.cache_file,
                max_chars=self.page_context_max_chars,
            )
        except Exception:
            return ""

    def _plans_for_prompt(self) -> str:
        with self._lock:
            plans = list(self._plans)
        now = time.monotonic()
        return "\n".join(plan.to_prompt_line(now) for plan in plans)

    def _call_planner(self, system_prompt: str, user_prompt: str) -> str:
        model = self.planning_model
        openai_compatible = False
        if cfg.is_deepseek_model(model):
            api_key = cfg.deepseek_api_key()
            api_url = cfg.deepseek_url()
            openai_compatible = True
        elif model.lower().startswith("charglm"):
            api_key = cfg.charglm_api_key()
            api_url = self.session.character_config.get("llm_url") or cfg.llm_url()
            openai_compatible = True
        else:
            api_key = ""
            api_url = cfg.llm_url()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        headers = {"Content-Type": "application/json"}
        if openai_compatible:
            base_url = api_url.rstrip("/")
            if not re.search(r"/v\d+$", base_url):
                base_url += "/v1"
            headers["Authorization"] = f"Bearer {api_key}"
            resp = requests.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 500,
                    "response_format": {"type": "json_object"},
                },
                headers=headers,
                timeout=45,
            )
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("usage", {})
            pt = int(usage.get("prompt_tokens", 0))
            ct = int(usage.get("completion_tokens", 0))
            if pt or ct:
                token_usage.record(model, "dialogue_plan", pt, ct)
            return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        resp = requests.post(
            f"{api_url}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 500},
            },
            timeout=45,
        )
        resp.raise_for_status()
        data = resp.json()
        pt = int(data.get("prompt_eval_count", 0))
        ct = int(data.get("eval_count", 0))
        if pt or ct:
            token_usage.record(model, "dialogue_plan", pt, ct)
        return data.get("message", {}).get("content", "").strip()


def _safe_context(obj) -> str:
    if obj is None or not hasattr(obj, "get_context"):
        return ""
    try:
        return obj.get_context() or ""
    except Exception:
        return ""


def _compact_character_prompt(character: dict, user_name: str, character_name: str) -> str:
    name = character_name
    personality = str(character.get("personality", "") or "").strip()
    return prompts.format_prompt(
        "dialogue_orchestrator.reply_character_prompt",
        user_name=user_name,
        name=name,
        personality=personality[:900],
    )


def _filter_prompt_history(messages: list[dict]) -> list[dict]:
    result: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = str(msg.get("content", "") or "").strip()
        if not content:
            continue
        result.append({"role": role, "content": content[:500]})
    return result


def _looks_like_system_design_topic(user_text: str, decision: DialogueDecision) -> bool:
    text = " ".join(
        part for part in (user_text, decision.topic, decision.intent) if part
    ).lower()
    markers = (
        "系统",
        "对话",
        "框架",
        "架构",
        "测试",
        "模型",
        "小模型",
        "planner",
        "impulse",
        "schedule",
        "prompt",
        "llm",
        "ai",
        "人格",
    )
    return any(marker in text for marker in markers)


def _normalize_context_use(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"screen", "page", "both", "none"}:
        return normalized
    if normalized in {"web", "网页", "browser"}:
        return "page"
    if normalized in {"屏幕", "desktop"}:
        return "screen"
    return "none"


def _format_history(messages: list[dict], *, user_name: str, character_name: str) -> str:
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "user":
            speaker = user_name
        elif role == "assistant":
            speaker = character_name
        else:
            speaker = "system"
        content = str(msg.get("content", "") or "").strip()
        if content:
            lines.append(f"{speaker}: {content[:500]}")
    return "\n".join(lines)


def _extract_json_object(text: str) -> dict | None:
    stripped = text.strip()
    if not stripped:
        return None
    stripped = stripped.lstrip("\ufeff")
    if stripped.endswith("</think>"):
        stripped = stripped[: -len("</think>")].strip()
    if "</think>" in stripped:
        stripped = stripped.split("</think>")[-1].strip()
    try:
        value = json.loads(stripped)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    code_match = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
    if code_match:
        try:
            value = json.loads(code_match.group(1).strip())
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass

    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = stripped[start:i + 1]
                try:
                    value = json.loads(candidate)
                    return value if isinstance(value, dict) else None
                except json.JSONDecodeError:
                    return None
    return None
