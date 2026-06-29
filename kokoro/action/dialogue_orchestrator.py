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

from kokoro.core import config as cfg
from kokoro.action import edge_cache
from kokoro.core import prompts
from kokoro.core import scene as scene_mod
from kokoro.action import screen_interest
from kokoro.core import token_usage

_AGENT_CAPABILITY_CACHE: str = ""
_AGENT_CAPABILITY_LOCK = threading.Lock()

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
class STTPoolTurnDecision:
    action: str = "wait"
    consumed_text: str = ""
    remaining_text: str = ""
    reply: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict, *, fallback_pool: str = "") -> "STTPoolTurnDecision":
        action = str(data.get("action", "wait") or "wait").strip().lower()
        if action not in {"wait", "backchannel", "speak"}:
            action = "wait"
        consumed = str(data.get("consumed_text", "") or "").strip()
        remaining = str(data.get("remaining_text", "") or "").strip()
        reply = str(data.get("reply", "") or "").strip()
        if action == "wait":
            consumed = ""
            reply = ""
            remaining = remaining or fallback_pool
        if action in {"speak", "backchannel"} and not consumed:
            consumed = fallback_pool.strip()
            remaining = ""
        if action in {"speak", "backchannel"} and not reply:
            action = "wait"
            remaining = fallback_pool.strip()
            consumed = ""
        return cls(
            action=action,
            consumed_text=consumed,
            remaining_text=remaining,
            reply=reply,
            notes=str(data.get("notes", "") or "").strip(),
        )


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
        result = cls(
            action=action,
            delay_seconds=max(0.0, delay),
            intent=str(data.get("intent", "") or "").strip(),
            topic=str(data.get("topic", "") or "").strip(),
            utterance_mode=str(data.get("utterance_mode", "normal") or "normal").strip(),
            context_use=_normalize_context_use(str(data.get("context_use", "none") or "none")),
            memory_policy=str(data.get("memory_policy", "normal") or "normal").strip(),
            notes=str(data.get("notes", "") or "").strip(),
        )
        if result.utterance_mode == "silent" and result.action in {"speak", "backchannel"}:
            result.action = "silence"
        return result


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
        proactive_section = config.get("proactive", {})
        if not isinstance(proactive_section, dict):
            proactive_section = {}

        self.enabled = bool(section.get("enabled", True))
        self.planning_model = str(
            section.get("planning_model")
            or proactive_section.get("planning_model")
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
        self.random_mc_enabled = scene_mod.random_mc_enabled(config)
        self._last_random_mc_signature = ""
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
        decision = self._apply_scene_guardrails(event, decision)

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

    def decide_stt_pool_turn(self, *, pool_text: str, extra_context: str = "") -> STTPoolTurnDecision:
        """Let the dialogue model decide whether an accumulated STT pool is ready."""
        pool_text = str(pool_text or "").strip()
        if not pool_text:
            return STTPoolTurnDecision(action="wait")

        system_prompt = _stt_pool_system_prompt()
        user_prompt = self._build_stt_pool_user_prompt(pool_text=pool_text, extra_context=extra_context)
        try:
            raw = self._call_planner(system_prompt, user_prompt)
            data = _extract_json_object(raw)
            if not data:
                raise ValueError("stt pool planner did not return JSON object")
            decision = STTPoolTurnDecision.from_dict(data, fallback_pool=pool_text)
        except Exception as exc:
            logger.warning("stt pool dialogue decision failed: %s", exc)
            decision = STTPoolTurnDecision(action="wait", remaining_text=pool_text, notes=type(exc).__name__)

        if self.log_decisions:
            print(f"\n  [dialogue-pool] action={decision.action}")
            if decision.consumed_text:
                print(f"  [dialogue-pool] consumed={decision.consumed_text[:120]}")
            if decision.remaining_text:
                print(f"  [dialogue-pool] remaining={decision.remaining_text[:120]}")
            if decision.notes:
                print(f"  [dialogue-pool] notes={decision.notes[:180]}")
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
        try:
            reason = (
                f"我感知到这段输入，快速反应路径选择了 {decision.action}。"
                f"{decision.notes or decision.intent or ''}"
            ).strip()
            record = getattr(self.session, "record_dialogue_observation", None)
            if callable(record):
                record(text, action=decision.action, reason=reason)
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
        agent_cap = _get_agent_capability_prompt()
        if agent_cap:
            messages.append({"role": "system", "content": agent_cap})
        inner_stream = _safe_context(getattr(self.session, "inner_stream", None))
        if inner_stream:
            messages.append({"role": "system", "content": inner_stream})
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
        metadata = {}
        extra_context = self._cache_overview_for_planner()
        if self.random_mc_enabled:
            signature = self._page_signature()
            if signature and signature != self._last_random_mc_signature:
                self._last_random_mc_signature = signature
                metadata["random_mc_page_changed"] = True
        return DialogueEvent(
            type="context_cache",
            source=reason,
            extra_context=extra_context,
            metadata=metadata,
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

    def _apply_scene_guardrails(self, event: DialogueEvent, decision: DialogueDecision) -> DialogueDecision:
        if not self.random_mc_enabled:
            return decision
        page = self._page_context_for_generator()
        if not page:
            return decision
        text = event.text or ""
        page_related = (
            event.metadata.get("random_mc_page_changed")
            or any(token in text for token in ("页面", "网页", "当前页", "这页", "浏览器", "MC", "Minecraft", "mc", "模组", "整合包"))
        )
        if page_related:
            decision.context_use = "page" if decision.context_use != "screen" else "both"
            if event.type == "context_cache" and decision.action in ("silence", "observe"):
                decision.action = "speak"
                decision.utterance_mode = "normal"
                decision.intent = decision.intent or "讨论随机 MC 百科新页面"
                decision.topic = decision.topic or "随机 MC 页面"
            decision.notes = (decision.notes + "；" if decision.notes else "") + "随机 MC 场景强制围绕当前网页"
        return decision

    def _page_signature(self) -> str:
        if not self.edge_cache_config.enabled:
            return ""
        data = edge_cache.read_cache(self.edge_cache_config.cache_file)
        if not data or data.get("error"):
            return ""
        tab = data.get("tab") if isinstance(data.get("tab"), dict) else {}
        return "|".join(
            [
                str(tab.get("url") or ""),
                str(tab.get("title") or ""),
                str(data.get("text") or "")[:500],
            ]
        )

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
        inner_stream = _safe_context(getattr(self.session, "inner_stream", None))
        plans = self._plans_for_prompt()
        extra_context = event.extra_context or "无"
        autonomy_hint = self._autonomy_hint_for_event(event)
        if autonomy_hint:
            extra_context = f"{extra_context}\n\n{autonomy_hint}"

        return prompts.format_prompt(
            "dialogue_orchestrator.planner_user",
            user_name=self.session.user_name,
            name=self.session.character_name,
            timestamp=now,
            event_type=event.type,
            speaker=event.source,
            event_text=event.text or "空",
            extra_context=extra_context,
            profile=profile,
            system_prompt=self.session.system_prompt[:self.max_character_prompt_chars] if self.max_character_prompt_chars else "已省略",
            summary=summary,
            recent_history=recent or "无",
            cognition_context=cognition or "无",
            inner_stream_context=inner_stream or "无",
            pending_plans=plans or "无",
        )

    def _autonomy_hint_for_event(self, event: DialogueEvent) -> str:
        suffix = prompts.get("dialogue_orchestrator.autonomy_hint_context_cache_suffix", "") if event.type == "context_cache" else ""
        return prompts.format_prompt(
            "dialogue_orchestrator.autonomy_hint",
            name=self.session.character_name,
            user_name=self.session.user_name,
            context_cache_suffix=suffix,
        )

    def _build_stt_pool_user_prompt(self, *, pool_text: str, extra_context: str = "") -> str:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        recent = _format_history(
            self.session.history[-self.max_recent_messages:],
            user_name=getattr(self.session, "user_name", "??"),
            character_name=self.session.character_name,
        )
        character = self.session.character_data
        profile_parts = []
        for key in ("name", "description", "personality", "relationship"):
            value = str(character.get(key, "") or "").strip()
            if value:
                profile_parts.append(f"{key}: {value[:self.max_profile_field_chars]}")
        profile = "\n\n".join(profile_parts) or "?"
        cognition = _safe_context(getattr(self.session, "cognition", None)) or "?"
        inner_stream = _safe_context(getattr(self.session, "inner_stream", None)) or "?"
        return prompts.format_prompt(
            "dialogue_orchestrator.stt_pool_user",
            timestamp=now,
            user_name=self.session.user_name,
            name=self.session.character_name,
            profile=profile,
            recent_history=recent or "?",
            cognition=cognition,
            inner_stream=inner_stream,
            extra_context=extra_context or "?",
            pool_text=pool_text,
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
        from kokoro.core import deepseek_api

        model = self.planning_model
        openai_compatible = False
        if cfg.is_deepseek_model(model):
            openai_compatible = True
        elif model.lower().startswith("charglm"):
            openai_compatible = True

        if openai_compatible:
            return deepseek_api.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=model,
                temperature=0.2,
                max_tokens=500,
                json_mode=True,
                function="dialogue_plan",
            )["content"]

        resp = requests.post(
            f"{cfg.llm_url().rstrip('/')}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
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


def _stt_pool_system_prompt() -> str:
    return prompts.get("dialogue_orchestrator.stt_pool_system", "")


def _compact_character_prompt(character: dict, user_name: str, character_name: str) -> str:
    name = character_name
    personality = str(character.get("personality", "") or "").strip()
    return prompts.format_prompt(
        "dialogue_orchestrator.reply_character_prompt",
        user_name=user_name,
        name=name,
        personality=personality[:900],
    )


def clean_generated_reply(text: str, character_name: str = "") -> str:
    """Remove common roleplay artifacts from generated dialogue text."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    names = [character_name, character_name.split("·")[0] if character_name else ""]
    for name in [n for n in names if n]:
        cleaned = re.sub(rf"^\s*{re.escape(name)}\s*[：:]\s*", "", cleaned)
    cleaned = re.sub(r"^\s*(?:台词|回复|回答)\s*[：:]\s*", "", cleaned)
    lines: list[str] = []
    action_markers = (
        "微微", "轻轻", "抬头", "低头", "偏过头", "歪了歪头", "看着", "望着",
        "沉默", "停了一拍", "没有出声", "目光", "笑了笑",
    )
    for raw in cleaned.splitlines():
        line = raw.strip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if line.startswith(("---", "——")) and len(line) <= 6:
            continue
        if any(marker in line for marker in action_markers) and not any(ch in line for ch in "？?"):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    return cleaned


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
        "proactive",
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


def _get_agent_capability_prompt() -> str:
    """Return agent capability prompt if agent tools are enabled."""
    global _AGENT_CAPABILITY_CACHE
    with _AGENT_CAPABILITY_LOCK:
        if _AGENT_CAPABILITY_CACHE != "":
            return _AGENT_CAPABILITY_CACHE

    section = cfg.get("agent", {})
    if not isinstance(section, dict) or not section.get("enabled", False):
        with _AGENT_CAPABILITY_LOCK:
            _AGENT_CAPABILITY_CACHE = ""
        return ""

    prompt_text = prompts.get("tool_calling.agent_capability", "")
    if not prompt_text:
        return ""

    suffix = prompts.get("tool_calling.system_suffix", "")
    result = f"{prompt_text}\n\n{suffix}" if suffix else prompt_text
    with _AGENT_CAPABILITY_LOCK:
        _AGENT_CAPABILITY_CACHE = result
    return result


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
        repaired = _repair_json_object_text(stripped)
        if repaired:
            try:
                value = json.loads(repaired)
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                pass

    code_match = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
    if code_match:
        block = code_match.group(1).strip()
        try:
            value = json.loads(block)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            repaired = _repair_json_object_text(block)
            if repaired:
                try:
                    value = json.loads(repaired)
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
                    repaired = _repair_json_object_text(candidate)
                    if repaired:
                        try:
                            value = json.loads(repaired)
                            return value if isinstance(value, dict) else None
                        except json.JSONDecodeError:
                            pass
                    return None
    return None


def _repair_json_object_text(text: str) -> str:
    candidate = text.strip()
    if not candidate:
        return ""
    candidate = candidate.replace("\u201c", '"').replace("\u201d", '"')
    candidate = candidate.replace("\u2018", "'").replace("\u2019", "'")
    candidate = candidate.replace("\uff1a", ":").replace("\uff0c", ",")
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        candidate = candidate[start:end + 1]
    else:
        return ""
    if "'" in candidate and '"' not in candidate:
        candidate = re.sub(r"(?<!\\)'([^'\\]*(?:\\.[^'\\]*)*)'", r'"\1"', candidate)
    candidate = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)', r'\1"\2"\3', candidate)
    return candidate
