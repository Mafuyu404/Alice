"""LLM-directed multi-character dialogue orchestration.

This module replaces the old round-robin multi-chat experiment. A third-person
planner decides which character, if any, should speak next; each selected
character still uses its own ChatSession, prompt, memory, cognition, and emotion.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

import requests

from kokoro import agent_loop
from kokoro import chat_session
from kokoro import config as _cfg
from kokoro import edge_cache
from kokoro import memory as _mem
from kokoro import prompts
from kokoro import scene as _scene
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


MULTI_DIALOGUE_PACE_GUIDANCE = """

节奏补充：
- 无人值守或角色互聊时，优先像同场的人自然接话，不要每句都道谢、确认、总结或征求许可。
- 角色之间可以有轻微分歧、吐槽、反问、抢白和不完全同意；不要为了礼貌把每句话磨平。
- character_utterance 后，如果另一名角色有明确态度、想挑刺、想纠正或想把话题拽向自己关心的点，可以选择 speak 或 backchannel。
- 让对话往前走：接上一句里的具体词、判断或漏洞，不要只说“你说得对”“确实如此”“我明白了”。
- 发言保持短而有钩子，留给下一位角色可接的点。
"""

MULTI_DIALOGUE_PACE_GUIDANCE = (
    MULTI_DIALOGUE_PACE_GUIDANCE
    + "\n【节奏约束】\n"
    "- 分歧、吐槽和反问只能针对已出现的具体话语或已证实上下文，不能为了好玩新造事实。\n"
    "- 如果上一句已经开始把话题带向未证实的共同经历，下一步优先 silence 或把话题拉回用户/页面/屏幕中的实际内容。\n"
    "- 没有新证据时，不要连续让两个角色围绕同一个虚构细节互相加码。\n"
)


FACT_ANCHORED_MULTI_DIALOGUE_GUIDANCE = (
    "\n\n【事实锚定规则】\n"
    "- 角色可以有语气、吐槽和态度，但不能发明具体事实。\n"
    "- 不要把未在用户输入、网页/屏幕缓存、角色设定、认知或长期记忆中出现过的内容说成已经发生。"
    "包括但不限于：一起修过 bug、某个原版代码、变量名、作者、会议、宠物、页面上有某标签或代码结构。\n"
    "- 上一轮角色如果编出了未证实事实，下一轮不能继续承认或扩写；要把它降级成玩笑、猜测，或直接转回有证据的内容。\n"
    "- 当证据不足时，说“不确定”“页面里没看到足够信息”比编一个具体场景更好。\n"
)


RANDOM_MC_MULTI_DIALOGUE_GUIDANCE = (
    "\n\n【随机 MC 直播节奏】\n"
    "- 当前是随机 MC 百科页面场景时，网页缓存是节目素材，不是一次性回答材料。\n"
    "- 只要网页缓存里有具体内容，idle_tick 不应轻易 silence；优先让角色继续介绍、评价、吐槽、比较或追问页面中的明确内容。\n"
    "- 页面切换到新条目时必须自然转向新页面；页面未切换时可以换角度继续讲同一页，例如玩法价值、依赖关系、作者信息、版本兼容、适合人群、槽点和疑问。\n"
    "- 角色可以自言自语或互相接话，不必等待真冬继续点名；这是直播/讲解场景，需要维持活跃度。\n"
    "- 为了节目效果可以有态度和推测，但具体事实必须来自网页缓存、对话或角色设定；不确定就明确说不确定。"
)


@dataclass
class HistoryEntry:
    speaker: str
    text: str
    character_id: str = ""


@dataclass
class MultiDialogueEvent:
    type: str
    text: str = ""
    speaker: str = ""
    source_id: str = ""
    extra_context: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class MultiDialogueDecision:
    action: str = "silence"
    speaker_id: str = ""
    target: str = ""
    delay_seconds: float = 0.0
    utterance_mode: str = "normal"
    intent: str = ""
    topic: str = ""
    context_use: str = "none"
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict, valid_speakers: set[str]) -> "MultiDialogueDecision":
        action = str(data.get("action", "silence") or "silence").strip().lower()
        if action not in SUPPORTED_ACTIONS:
            action = "silence"
        speaker_id = str(data.get("speaker_id", "") or "").strip()
        if speaker_id not in valid_speakers:
            speaker_id = ""
        if action in ("speak", "backchannel", "schedule") and not speaker_id:
            action = "silence"
        try:
            delay = float(data.get("delay_seconds", 0) or 0)
        except (TypeError, ValueError):
            delay = 0.0
        return cls(
            action=action,
            speaker_id=speaker_id,
            target=str(data.get("target", "") or "").strip(),
            delay_seconds=max(0.0, delay),
            utterance_mode=str(data.get("utterance_mode", "normal") or "normal").strip(),
            intent=str(data.get("intent", "") or "").strip(),
            topic=str(data.get("topic", "") or "").strip(),
            context_use=_normalize_context_use(str(data.get("context_use", "none") or "none")),
            notes=str(data.get("notes", "") or "").strip(),
        )


@dataclass
class MultiDialoguePlan:
    id: str
    due_at: float
    decision: MultiDialogueDecision
    created_from: str = ""

    def to_prompt_line(self, now: float, names: dict[str, str]) -> str:
        remaining = max(0.0, self.due_at - now)
        name = names.get(self.decision.speaker_id, self.decision.speaker_id or "未知角色")
        topic = self.decision.topic or "无话题"
        intent = self.decision.intent or "无意图"
        return f"- {self.id}: {remaining:.0f}秒后，{name}，话题={topic}，意图={intent}"


@dataclass
class MultiChatConfig:
    character_ids: list[str] = field(default_factory=lambda: ["alice"])
    max_history: int = 30
    model: str = ""
    planning_model: str = ""
    max_delay_seconds: float = 120.0
    max_auto_followups: int = 0
    log_decisions: bool = True
    enable_tools: bool = False
    tts_engines: dict[str, object] = field(default_factory=dict)
    portrait_worker: object = None


@dataclass
class PreparedMultiTurn:
    character_id: str
    character_name: str
    reply: str
    trigger_text: str = ""
    history_len: int = 0


class MultiChatOrchestrator:
    """Third-person planner for a user plus multiple AI characters."""

    def __init__(self, config: MultiChatConfig | None = None, runtime_config: dict | None = None):
        self.config = config or MultiChatConfig()
        self.runtime_config = runtime_config or _cfg.load()
        multi_section = self.runtime_config.get("multi_dialogue", {})
        if not isinstance(multi_section, dict):
            multi_section = {}
        dialogue_section = self.runtime_config.get("dialogue", {})
        if not isinstance(dialogue_section, dict):
            dialogue_section = {}
        impulse_section = self.runtime_config.get("impulse", {})
        if not isinstance(impulse_section, dict):
            impulse_section = {}

        self.planning_model = (
            self.config.planning_model
            or str(multi_section.get("planning_model") or "")
            or str(dialogue_section.get("planning_model") or "")
            or str(impulse_section.get("planning_model") or "")
            or _cfg.llm_model()
        )
        self.max_delay_seconds = max(1.0, float(multi_section.get("max_delay_seconds", self.config.max_delay_seconds)))
        self.max_auto_followups = max(0, int(multi_section.get("max_auto_followups", self.config.max_auto_followups)))
        self.log_decisions = bool(multi_section.get("log_decisions", self.config.log_decisions))
        self.screen_context_max_chars = max(200, int(multi_section.get("screen_context_max_chars", 1200)))
        self.page_context_max_chars = max(500, int(multi_section.get("page_context_max_chars", 2500)))
        self.context_idle_min_score = max(0.0, float(multi_section.get("context_idle_min_score", 70.0)))
        self.edge_cache_config = edge_cache.config_from_dict(self.runtime_config)
        self.random_mc_enabled = _scene.random_mc_enabled(self.runtime_config)
        self._last_random_mc_signature = ""

        self.shared_history: list[HistoryEntry] = []
        self.memory_backend = _mem.create_backend(self.runtime_config)
        self.user_name = _cfg.user_name()
        self.last_auto_action = ""
        self.scene = (
            _scene.SceneType.MULTI_LIVE
            if _scene.live_enabled(self.runtime_config)
            else _scene.SceneType.MULTI_CHAT
        )

        self.sessions: dict[str, chat_session.ChatSession] = {}
        self.order: list[str] = []
        for cid in self.config.character_ids:
            session = chat_session.load_session(
                cid,
                self.memory_backend,
                max_history=self.config.max_history,
            )
            session._scene = self.scene
            self.sessions[cid] = session
            self.order.append(cid)
        if len(self.order) < 1:
            raise ValueError("multi dialogue requires at least one character")

        self._plans: list[MultiDialoguePlan] = []
        self._lock = threading.Lock()

    @property
    def character_names(self) -> dict[str, str]:
        return {cid: session.character_name for cid, session in self.sessions.items()}

    @property
    def participant_names(self) -> list[str]:
        return [self.user_name] + [self.sessions[cid].character_name for cid in self.order]

    def close(self) -> None:
        close = getattr(self.memory_backend, "close", None)
        if callable(close):
            close()

    def add_user_message(self, text: str) -> None:
        self.shared_history.append(HistoryEntry(speaker=self.user_name, text=text, character_id=""))

    def add_ai_message(self, character_id: str, text: str) -> None:
        name = self.sessions[character_id].character_name
        self.shared_history.append(HistoryEntry(speaker=name, text=text, character_id=character_id))

    def decide(self, event: MultiDialogueEvent, *, log: bool = True) -> MultiDialogueDecision:
        system_prompt = prompts.get("multi_dialogue_orchestrator.planner_system", "")
        if system_prompt:
            system_prompt += MULTI_DIALOGUE_PACE_GUIDANCE
            system_prompt += FACT_ANCHORED_MULTI_DIALOGUE_GUIDANCE
            if self.random_mc_enabled:
                system_prompt += RANDOM_MC_MULTI_DIALOGUE_GUIDANCE
        user_prompt = self._build_planner_user_prompt(event)
        try:
            raw = self._call_planner(system_prompt, user_prompt)
            data = _extract_json_object(raw)
            if not data:
                raw = self._call_planner(
                    system_prompt,
                    user_prompt + "\n\n上一次输出不是合法 JSON。现在只输出一个 JSON 对象，不要解释，不要 Markdown。",
                )
                data = _extract_json_object(raw)
            if not data:
                raise ValueError("multi planner did not return JSON object")
            decision = MultiDialogueDecision.from_dict(data, set(self.sessions.keys()))
        except Exception as exc:
            if log:
                logger.warning("multi dialogue planner failed: %s", exc)
            else:
                logger.debug("multi dialogue planner failed during prefetch: %s", exc)
            decision = self._fallback_decision(event, notes=f"planner fallback: {type(exc).__name__}")

        if decision.action == "schedule":
            decision.delay_seconds = min(decision.delay_seconds, self.max_delay_seconds)
        decision = self._apply_context_guardrails(event, decision)

        if self.log_decisions and log:
            speaker = self.character_names.get(decision.speaker_id, "-")
            topic = f" topic={decision.topic}" if decision.topic else ""
            print(
                f"\n  [multi-dialogue] action={decision.action} speaker={speaker} "
                f"mode={decision.utterance_mode} context={decision.context_use}{topic}"
            )
            if decision.notes:
                print(f"  [multi-dialogue] notes={decision.notes[:180]}")
        return decision

    def user_turn(self, user_text: str, *, auto_followups: int | None = None) -> list[tuple[str, str, str]]:
        self.add_user_message(user_text)
        event = MultiDialogueEvent(
            type="user_utterance",
            text=user_text,
            speaker=self.user_name,
            source_id="",
        )
        turns = self._execute_event(event)
        followups = self.max_auto_followups if auto_followups is None else max(0, auto_followups)
        if self.random_mc_enabled and self._text_asks_page(user_text) and self._page_context_for_generator():
            followups = max(followups, 1)
        for _ in range(followups):
            if not turns:
                break
            last_id, last_name, last_reply = turns[-1]
            next_event = MultiDialogueEvent(
                type="character_utterance",
                text=last_reply,
                speaker=last_name,
                source_id=last_id,
            )
            next_turns = self._execute_event(next_event, allow_schedule=False)
            if not next_turns:
                break
            turns.extend(next_turns)
        return turns

    def auto_turn(self) -> tuple[str, str, str]:
        self.last_auto_action = ""
        plan = self.pop_due_plan()
        if plan is not None:
            turn = self._execute_decision(plan.decision, trigger_text=plan.decision.topic or plan.decision.intent)
            self.last_auto_action = plan.decision.action
            return turn or ("", "", "")

        metadata = {}
        if self.random_mc_enabled:
            signature = self._page_signature()
            if signature and signature != self._last_random_mc_signature:
                self._last_random_mc_signature = signature
                metadata["random_mc_page_changed"] = True
        event = MultiDialogueEvent(
            type="idle_tick",
            speaker="system",
            extra_context=self._cache_overview_for_planner(),
            metadata=metadata,
        )
        decision = self.decide(event)
        self.last_auto_action = decision.action
        if decision.action == "cancel_plan":
            self.cancel_plans()
            return "", "", ""
        if decision.action in ("silence", "observe"):
            return "", "", ""
        if decision.action == "schedule":
            self.add_plan(decision, created_from=event.text)
            return "", "", ""
        turn = self._execute_decision(decision, trigger_text=event.text)
        return turn or ("", "", "")

    def prepare_followup_turn(self, source_id: str, speaker: str, text: str) -> PreparedMultiTurn | None:
        event = MultiDialogueEvent(
            type="character_utterance",
            text=text,
            speaker=speaker,
            source_id=source_id,
        )
        turns = self._execute_event(event, allow_schedule=False, commit=False, log=False)
        if not turns:
            return None
        cid, name, reply = turns[0]
        return PreparedMultiTurn(
            character_id=cid,
            character_name=name,
            reply=reply,
            trigger_text=text,
            history_len=len(self.shared_history),
        )

    def commit_prepared_turn(self, prepared: PreparedMultiTurn | None) -> tuple[str, str, str]:
        if prepared is None or not prepared.reply:
            return "", "", ""
        if prepared.history_len != len(self.shared_history):
            return "", "", ""
        self.add_ai_message(prepared.character_id, prepared.reply)
        return prepared.character_id, prepared.character_name, prepared.reply

    def auto_cycle(self, rounds: int = 5, init_prompt: str = "") -> list[tuple[str, str, str]]:
        turns: list[tuple[str, str, str]] = []
        if init_prompt:
            turns.extend(self.user_turn(init_prompt))
        for _ in range(rounds):
            cid, name, reply = self.auto_turn()
            if not reply:
                break
            turns.append((cid, name, reply))
        return turns

    def cancel_plans(self) -> None:
        with self._lock:
            self._plans.clear()

    def add_plan(self, decision: MultiDialogueDecision, created_from: str = "") -> MultiDialoguePlan:
        plan = MultiDialoguePlan(
            id=str(uuid.uuid4())[:8],
            due_at=time.monotonic() + min(decision.delay_seconds, self.max_delay_seconds),
            decision=decision,
            created_from=created_from,
        )
        with self._lock:
            self._plans.append(plan)
            self._plans.sort(key=lambda item: item.due_at)
        return plan

    def pop_due_plan(self) -> MultiDialoguePlan | None:
        now = time.monotonic()
        with self._lock:
            if not self._plans or self._plans[0].due_at > now:
                return None
            return self._plans.pop(0)

    def _execute_event(
        self,
        event: MultiDialogueEvent,
        *,
        allow_schedule: bool = True,
        commit: bool = True,
        log: bool = True,
    ) -> list[tuple[str, str, str]]:
        decision = self.decide(event, log=log)
        if decision.action == "cancel_plan":
            self.cancel_plans()
            return []
        if decision.action in ("silence", "observe"):
            return []
        if decision.action == "schedule":
            if allow_schedule:
                self.add_plan(decision, created_from=event.text)
            return []
        turn = self._execute_decision(decision, trigger_text=event.text, commit=commit)
        return [turn] if turn else []

    def _execute_decision(
        self,
        decision: MultiDialogueDecision,
        *,
        trigger_text: str = "",
        commit: bool = True,
    ) -> tuple[str, str, str] | None:
        if decision.speaker_id not in self.sessions:
            return None
        messages = self.build_reply_messages(decision, trigger_text=trigger_text)
        session = self.sessions[decision.speaker_id]
        model = self.config.model or session.character_config.get("llm_model") or _cfg.llm_model()
        api_base_url = session.character_config.get("llm_url") or None
        api_key = _api_key_for_model(model)
        result = agent_loop.agent_chat(
            messages,
            model,
            agent_config=None,
            cancel_event=threading.Event(),
            character_config=session.character_config,
            api_base_url=api_base_url,
            api_key=api_key,
            usage_callback=token_usage.make_callback(model, "multi_dialogue_chat"),
            capture=True,
        )
        reply = result.reply.strip()
        if not reply:
            return None
        if commit:
            self.add_ai_message(decision.speaker_id, reply)
            self._remember_for_speaker(decision.speaker_id, trigger_text, reply, decision=decision)
        return decision.speaker_id, session.character_name, reply

    def build_reply_messages(self, decision: MultiDialogueDecision, *, trigger_text: str = "") -> list[dict]:
        session = self.sessions[decision.speaker_id]
        character_prompt = prompts.format_prompt(
            "multi_dialogue_orchestrator.reply_character_prompt",
            participants="、".join(self.participant_names),
            name=session.character_name,
            personality=str(session.character_data.get("personality", "") or "")[:900],
        )
        if character_prompt:
            character_prompt += MULTI_DIALOGUE_PACE_GUIDANCE
            character_prompt += FACT_ANCHORED_MULTI_DIALOGUE_GUIDANCE
            if self.random_mc_enabled:
                character_prompt += RANDOM_MC_MULTI_DIALOGUE_GUIDANCE
        action_instruction = (
            prompts.get("multi_dialogue_orchestrator.generator_backchannel_instruction", "")
            if decision.action == "backchannel"
            else prompts.format_prompt("multi_dialogue_orchestrator.generator_speak_instruction", name=session.character_name)
        )
        boundary = prompts.format_prompt(
            "multi_dialogue_orchestrator.generator_context",
            participants="、".join(self.participant_names),
            name=session.character_name,
            target=decision.target or "当前最需要回应的人",
            action=decision.action,
            mode=decision.utterance_mode or decision.action,
            intent=decision.intent or "无",
            topic=decision.topic or "无",
            context_use=decision.context_use,
            action_instruction=action_instruction,
        )
        messages: list[dict] = [
            {"role": "system", "content": character_prompt or session.system_prompt},
            {"role": "system", "content": boundary},
        ]

        scene_context = self._scene_context_for(session.character_name)
        if scene_context:
            messages.append({"role": "system", "content": scene_context})
        cache_context = self.context_for_decision(decision)
        if cache_context:
            messages.append({"role": "system", "content": cache_context})
        anti_fiction = (
            "\u3010\u4e8b\u5b9e\u8fb9\u754c\u3011\n"
            "\u53ea\u80fd\u8ba8\u8bba\u6700\u8fd1\u5bf9\u8bdd\u3001\u660e\u786e\u6ce8\u5165\u7684\u7f51\u9875/\u5c4f\u5e55\u7f13\u5b58\u3001\u89d2\u8272\u7a33\u5b9a\u8bbe\u5b9a\u548c\u957f\u671f\u8bb0\u5fc6\u4e2d\u5df2\u7ecf\u5b58\u5728\u7684\u4e8b\u5b9e\u3002"
            "\u4e0d\u8981\u7f16\u9020\u9875\u9762\u5185\u5bb9\u3001\u4ee3\u7801\u7ed3\u6784\u3001\u53d8\u91cf\u540d\u3001\u8fc7\u53bb\u4e00\u8d77\u4fee bug\u3001\u5f00\u4f1a\u3001\u4f5c\u8005\u6216\u5176\u4ed6\u672a\u51fa\u73b0\u7684\u7ecf\u5386\u3002"
            "\u5982\u679c\u9875\u9762/\u5c4f\u5e55\u7f13\u5b58\u4e3a\u7a7a\u6216\u4e0d\u591f\u5177\u4f53\uff0c\u8981\u76f4\u63a5\u8bf4\u770b\u4e0d\u5230\u8db3\u591f\u5185\u5bb9\uff0c\u4e0d\u80fd\u7528\u60f3\u8c61\u8865\u9f50\u3002"
        )
        messages.append({"role": "system", "content": anti_fiction})
        if self.random_mc_enabled and decision.context_use in ("page", "both"):
            messages.append({
                "role": "system",
                "content": (
                    "【随机 MC 讲解输出要求】\n"
                    "这是一段直播式页面讲解，不是一次性问答。请围绕网页缓存中明确出现的内容说 2-4 句，"
                    "至少包含一个具体页面信息点和一个角色自己的评价、疑问或吐槽。"
                    "不要只问“你对哪个感兴趣”，也不要因为真冬没继续说就收束。"
                    "如果信息不足，说明不足并点评已能看到的标题/栏目/条目，不要编造。"
                ),
            })
        memory_query = " ".join(part for part in (trigger_text, decision.topic, decision.intent) if part)
        memory_ctx = self._safe_memory_context(session.character_id, memory_query)
        if memory_ctx:
            messages.append({"role": "system", "content": memory_ctx})
        cognition_ctx = _safe_context(getattr(session, "cognition", None))
        if cognition_ctx:
            messages.append({"role": "system", "content": cognition_ctx})
        emotion_ctx = _safe_context(getattr(session, "emotion", None))
        if emotion_ctx:
            messages.append({"role": "system", "content": emotion_ctx})

        history = self._format_history(max_entries=self.config.max_history) or "无"
        user_prompt = (
            f"【最近共享对话】\n{history}\n\n"
            f"【当前触发内容】\n{trigger_text or decision.topic or '无'}\n\n"
            f"请只写{session.character_name}现在会说出口的话："
        )
        messages.append({"role": "user", "content": user_prompt})
        return messages

    def context_for_decision(self, decision: MultiDialogueDecision) -> str:
        parts: list[str] = []
        if decision.context_use in ("screen", "both"):
            screen = self._screen_context_for_generator()
            if screen:
                parts.append(prompts.format_prompt("multi_dialogue_orchestrator.screen_cache_context", screen=screen))
        if decision.context_use in ("page", "both"):
            page = self._page_context_for_generator()
            if page:
                parts.append(prompts.format_prompt("multi_dialogue_orchestrator.page_cache_context", page=page))
        return "\n\n".join(parts)

    def _build_planner_user_prompt(self, event: MultiDialogueEvent) -> str:
        extra_context = event.extra_context or ""
        if event.type == "user_utterance":
            page = self._page_context_for_generator()
            if page:
                extra_context = "\n\n".join(
                    part for part in (
                        extra_context,
                        f"\u7f51\u9875\u7f13\u5b58\u5019\u9009\uff1a\n{page[:self.page_context_max_chars]}",
                    )
                    if part
                )
        return prompts.format_prompt(
            "multi_dialogue_orchestrator.planner_user",
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            event_type=event.type,
            user_name=self.user_name,
            speaker=event.speaker or "\u672a\u77e5",
            source_id=event.source_id or "\u65e0",
            event_text=event.text or "\u7a7a",
            characters=self._characters_for_prompt(),
            recent_history=self._format_history(max_entries=self.config.max_history) or "\u65e0",
            runtime_context=self._runtime_context_for_prompt(),
            pending_plans=self._plans_for_prompt() or "\u65e0",
            extra_context=extra_context or "\u65e0",
        )

    def _apply_context_guardrails(
        self,
        event: MultiDialogueEvent,
        decision: MultiDialogueDecision,
    ) -> MultiDialogueDecision:
        text = event.text or ""
        asks_page = self._text_asks_page(text)
        asks_screen = any(
            token in text
            for token in ("\u5c4f\u5e55", "\u753b\u9762", "\u622a\u56fe", "\u7a97\u53e3", "screen")
        )
        if event.type == "user_utterance" and asks_page and self._page_context_for_generator():
            decision.context_use = "both" if decision.context_use == "screen" or asks_screen else "page"
            decision.notes = (
                decision.notes + "\uff1b" if decision.notes else ""
            ) + "\u7528\u6237\u660e\u786e\u8981\u6c42\u804a\u9875\u9762\uff0c\u5f3a\u5236\u4f7f\u7528\u7f51\u9875\u7f13\u5b58"
        elif self.random_mc_enabled and event.metadata.get("random_mc_page_changed") and self._page_context_for_generator():
            decision.context_use = "page" if decision.context_use != "screen" else "both"
            if decision.action in ("silence", "observe"):
                decision.action = "speak"
                decision.speaker_id = self._fallback_speaker_for_text(text)
                decision.target = self.user_name
                decision.utterance_mode = "normal"
                decision.intent = decision.intent or "讨论随机 MC 百科新页面"
                decision.topic = decision.topic or "随机 MC 页面"
            decision.notes = (decision.notes + "\uff1b" if decision.notes else "") + "随机 MC 页面变化，强制围绕当前网页"
        elif self.random_mc_enabled and event.type == "idle_tick" and self._page_context_for_generator():
            decision.context_use = "page" if decision.context_use != "screen" else "both"
            if decision.action in ("silence", "observe"):
                decision.action = "speak"
                decision.speaker_id = self._random_mc_idle_speaker()
                decision.target = self.user_name
                decision.utterance_mode = "normal"
                decision.intent = decision.intent or "keep random MC page commentary active"
                decision.topic = decision.topic or self._page_topic_hint()
            decision.notes = (decision.notes + "；" if decision.notes else "") + "random MC mode keeps page commentary active on idle ticks"
        elif event.type == "user_utterance" and asks_screen and self._screen_context_for_generator():
            decision.context_use = "both" if decision.context_use == "page" else "screen"
            decision.notes = (
                decision.notes + "\uff1b" if decision.notes else ""
            ) + "\u7528\u6237\u660e\u786e\u8981\u6c42\u770b\u5c4f\u5e55\uff0c\u5f3a\u5236\u4f7f\u7528\u5c4f\u5e55\u7f13\u5b58"
        return decision

    @staticmethod
    def _text_asks_page(text: str) -> bool:
        return any(
            token in text
            for token in (
                "页面", "网页", "当前页", "这页", "浏览器",
                "模组", "整合包", "MC", "Minecraft", "mc", "page", "webpage",
            )
        )

    def _random_mc_idle_speaker(self) -> str:
        recent = [item.character_id for item in self.shared_history[-3:] if item.character_id]
        if recent:
            candidates = [cid for cid in self.order if cid != recent[-1]]
            if candidates:
                return self._least_recent_speaker(candidates)
        return self._least_recent_speaker(self.order)

    def _least_recent_speaker(self, candidates: list[str]) -> str:
        recent_counts = {
            cid: sum(1 for item in self.shared_history[-8:] if item.character_id == cid)
            for cid in candidates
        }
        return min(candidates, key=lambda cid: recent_counts.get(cid, 0))

    def _page_topic_hint(self) -> str:
        data = edge_cache.read_cache(self.edge_cache_config.cache_file)
        if not isinstance(data, dict):
            return "random MC page"
        tab = data.get("tab")
        title = str((tab or {}).get("title") or "").strip() if isinstance(tab, dict) else ""
        return title[:60] if title else "random MC page"

    def _characters_for_prompt(self) -> str:
        lines: list[str] = []
        for cid in self.order:
            session = self.sessions[cid]
            character = session.character_data
            fields = [
                f"id={cid}",
                f"name={session.character_name}",
            ]
            for key in ("description", "personality", "relationship"):
                value = str(character.get(key, "") or "").strip()
                if value:
                    fields.append(f"{key}={value[:420]}")
            recent_count = sum(1 for item in self.shared_history[-8:] if item.character_id == cid)
            fields.append(f"最近8条内发言次数={recent_count}")
            lines.append("；".join(fields))
        return "\n".join(lines)

    def _runtime_context_for_prompt(self) -> str:
        lines: list[str] = []
        for cid in self.order:
            session = self.sessions[cid]
            chunks = []
            cognition_ctx = _safe_context(getattr(session, "cognition", None))
            emotion_ctx = _safe_context(getattr(session, "emotion", None))
            if cognition_ctx:
                chunks.append(cognition_ctx[:500])
            if emotion_ctx:
                chunks.append(emotion_ctx[:300])
            lines.append(f"{session.character_name}（{cid}）：{chr(10).join(chunks) if chunks else '无'}")
        return "\n\n".join(lines)

    def _format_history(self, max_entries: int = 20) -> str:
        entries = self.shared_history[-max_entries:] if max_entries else self.shared_history
        lines = []
        for entry in entries:
            if not entry.text:
                continue
            marker = "\u53f0\u8bcd\uff0c\u4e0d\u81ea\u52a8\u4f5c\u4e3a\u4e8b\u5b9e" if entry.character_id else "\u7528\u6237\u8f93\u5165"
            lines.append(f"{entry.speaker}\uff08{marker}\uff09\uff1a{entry.text[:700]}")
        return "\n".join(lines)

    def _scene_context_for(self, character_name: str) -> str:
        guidance = _scene.guidance_text(self.scene, self.user_name, character_name, self.runtime_config)
        if not guidance:
            return ""
        prefix = prompts.get("scene.prefix", "【当前场景：{scene_name}】")
        scene_label = prefix.format(scene_name=_scene.scene_name(self.scene))
        return f"{scene_label}\n{guidance}"

    def _plans_for_prompt(self) -> str:
        with self._lock:
            plans = list(self._plans)
        now = time.monotonic()
        names = self.character_names
        return "\n".join(plan.to_prompt_line(now, names) for plan in plans)

    def _fallback_decision(self, event: MultiDialogueEvent, *, notes: str = "") -> MultiDialogueDecision:
        if event.type == "user_utterance":
            return MultiDialogueDecision(
                action="speak",
                speaker_id=self._fallback_speaker_for_text(event.text),
                target=self.user_name,
                intent="planner失败时由较少发言的角色接住用户输入",
                topic=event.text[:40],
                notes=notes,
            )
        if event.type == "character_utterance":
            speaker_id = self._fallback_followup_speaker(event.source_id)
            if speaker_id:
                return MultiDialogueDecision(
                    action="speak",
                    speaker_id=speaker_id,
                    target=event.speaker,
                    intent="planner失败时由另一名角色自然接上一句",
                    topic=event.text[:40],
                    notes=notes,
                )
        return MultiDialogueDecision(action="silence", notes=notes)

    def _fallback_speaker_for_text(self, text: str) -> str:
        lowered = text.lower()
        for cid, session in self.sessions.items():
            if session.character_name and session.character_name.lower() in lowered:
                return cid
        recent_counts = {
            cid: sum(1 for item in self.shared_history[-8:] if item.character_id == cid)
            for cid in self.order
        }
        return min(self.order, key=lambda cid: recent_counts.get(cid, 0))

    def _fallback_followup_speaker(self, source_id: str) -> str:
        candidates = [cid for cid in self.order if cid != source_id]
        if not candidates:
            candidates = list(self.order)
        if not candidates:
            return ""
        recent_counts = {
            cid: sum(1 for item in self.shared_history[-8:] if item.character_id == cid)
            for cid in candidates
        }
        return min(candidates, key=lambda cid: recent_counts.get(cid, 0))

    def _remember_for_speaker(
        self,
        speaker_id: str,
        trigger_text: str,
        reply: str,
        *,
        decision: MultiDialogueDecision | None = None,
    ) -> None:
        if decision is not None and decision.target != self.user_name:
            return
        if trigger_text and any(token in trigger_text for token in ("bug", "变量", "代码", "作者", "会议", "原版")):
            return
        session = self.sessions[speaker_id]
        try:
            session.remember(trigger_text, reply, async_store=True)
        except Exception:
            logger.exception("multi dialogue remember failed for %s", speaker_id)

    def _safe_memory_context(self, character_id: str, query: str) -> str:
        if not query:
            return ""
        try:
            return self.memory_backend.get_context(query, user_id=character_id) or ""
        except Exception:
            return ""

    def _call_planner(self, system_prompt: str, user_prompt: str) -> str:
        model = self.planning_model
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if _cfg.is_deepseek_model(model):
            api_url = _cfg.deepseek_url().rstrip("/v1")
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_cfg.deepseek_api_key()}"}
            resp = requests.post(
                f"{api_url}/v1/chat/completions",
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
                token_usage.record(model, "multi_dialogue_plan", pt, ct)
            return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        api_url = _cfg.llm_url()
        resp = requests.post(
            f"{api_url}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "format": "json",
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
            token_usage.record(model, "multi_dialogue_plan", pt, ct)
        return data.get("message", {}).get("content", "").strip()

    def _cache_overview_for_planner(self) -> str:
        parts: list[str] = []
        try:
            result, timestamp = screen_interest.get_cache().get()
        except Exception:
            result, timestamp = None, 0.0
        if result and result.content and result.score >= self.context_idle_min_score:
            age = max(0.0, time.time() - timestamp) if timestamp else 0.0
            parts.append(
                f"屏幕缓存候选：\n分数={result.score:.1f}，缓存年龄={age:.0f}秒，隐私={result.private}\n"
                f"{result.content[:self.screen_context_max_chars]}"
            )
        page = self._page_context_for_generator()
        if page:
            parts.append(f"网页缓存候选：\n{page[:self.page_context_max_chars]}")
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


def _api_key_for_model(model: str) -> str | None:
    lowered = model.lower()
    if lowered.startswith("charglm"):
        return _cfg.charglm_api_key() or None
    return None


def _safe_context(obj) -> str:
    if obj is None or not hasattr(obj, "get_context"):
        return ""
    try:
        return obj.get_context() or ""
    except Exception:
        return ""


def _normalize_context_use(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"screen", "page", "both", "none"}:
        return normalized
    if normalized in {"web", "网页", "browser"}:
        return "page"
    if normalized in {"屏幕", "desktop"}:
        return "screen"
    return "none"


def _extract_json_object(text: str) -> dict | None:
    stripped = text.strip().lstrip("\ufeff")
    if not stripped:
        return None
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
    for index in range(start, len(stripped)):
        ch = stripped[index]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    candidate = stripped[start:index + 1]
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
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        return candidate[start:end + 1]
    return ""
