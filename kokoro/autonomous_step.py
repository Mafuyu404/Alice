"""Unified autonomous action decision and execution.

This module keeps the AI as the decision maker.  Runtime code supplies the
recent reality, available capabilities, and hard execution boundaries; the LLM
chooses one next action.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from kokoro import config as cfg
from kokoro import deepseek_api
from kokoro import input_events
from kokoro import prompts
from kokoro.web_search_client import WebSearchClient, format_search_result

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutonomousDecision:
    action: str = "wait"
    reason: str = ""
    conversation_id: str = ""
    message: str = ""
    query: str = ""
    memory_note: str = ""
    cognition_note: str = ""
    target: str = ""
    tags: list[str] = field(default_factory=list)
    sticker_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutonomousDecision":
        action = str(data.get("action") or "wait").strip().lower()
        if action not in {"wait", "observe", "say_qq", "send_sticker", "search_web", "remember", "update_cognition"}:
            action = "wait"
        tags = data.get("tags")
        if not isinstance(tags, list):
            tags = []
        return cls(
            action=action,
            reason=str(data.get("reason") or "").strip(),
            conversation_id=str(data.get("conversation_id") or "").strip(),
            message=str(data.get("message") or "").strip(),
            query=str(data.get("query") or "").strip(),
            memory_note=str(data.get("memory_note") or data.get("event") or "").strip(),
            cognition_note=str(data.get("cognition_note") or data.get("cognition") or "").strip(),
            target=str(data.get("target") or "").strip(),
            tags=[str(tag).strip() for tag in tags if str(tag).strip()][:6],
            sticker_id=str(data.get("sticker_id") or "").strip(),
        )


class AutonomousStep:
    """Single AI-driven action loop for idle/QQ/search/memory/cognition choices."""

    def __init__(
        self,
        *,
        session,
        section: dict[str, Any] | None = None,
        search_section: dict[str, Any] | None = None,
    ) -> None:
        section = dict(section or {})
        search_section = dict(search_section or {})
        self.session = session
        self.enabled = bool(section.get("enabled", True))
        self.model = str(section.get("model") or "").strip() or cfg.dialogue_model() or cfg.llm_model()
        self.max_tokens = int(section.get("max_tokens", 512) or 512)
        self.min_interval_seconds = max(0.0, float(section.get("min_interval_seconds", 3.0) or 3.0))
        self.search_client = WebSearchClient(
            base_url=str(search_section.get("base_url") or "http://127.0.0.1:3000"),
            timeout=float(search_section.get("timeout", 45.0) or 45.0),
        )
        self.search_max_results = int(search_section.get("max_results", 5) or 5)
        self.search_max_event_chars = int(search_section.get("max_event_chars", 6000) or 6000)
        self._last_decide_at_by_scope: dict[str, float] = {}
        self._last_say_at_by_conversation: dict[str, float] = {}
        self._sticker_provider = None
        self._lock = threading.Lock()
        self._memory_reflector = None
        self._cognition_reflector = None

    def attach_reflectors(self, *, memory_reflector=None, cognition_reflector=None) -> None:
        self._memory_reflector = memory_reflector
        self._cognition_reflector = cognition_reflector

    def attach_sticker_provider(self, provider) -> None:
        self._sticker_provider = provider

    def mark_social_output(self, conversation_id: str) -> None:
        cid = str(conversation_id or "").strip()
        if cid:
            self._last_say_at_by_conversation[cid] = time.time()

    def decide(
        self,
        *,
        events: list[input_events.InputEvent] | None = None,
        context: dict[str, Any] | None = None,
        qq_packets: list[Any] | None = None,
        trigger_reason: str = "",
        capabilities: list[str] | None = None,
        cooldown_scope: str = "",
    ) -> AutonomousDecision:
        if not self.enabled:
            return AutonomousDecision(reason="autonomous step disabled")
        capabilities = capabilities or ["say_qq", "send_sticker", "search_web", "remember", "update_cognition", "observe", "wait"]
        scope = str(cooldown_scope or ("qq" if "say_qq" in capabilities else "background")).strip() or "default"
        now = time.monotonic()
        last_decide_at = self._last_decide_at_by_scope.get(scope, 0.0)
        if self.min_interval_seconds > 0 and now - last_decide_at < self.min_interval_seconds:
            return AutonomousDecision(reason="autonomous step cooldown")
        self._last_decide_at_by_scope[scope] = now
        context = self._enrich_context(
            events=events or [],
            context=context or {},
            qq_packets=qq_packets or [],
        )
        # Calculate silent streak for each active conversation
        silent_info = self._silent_streak_info(qq_packets or [])
        context["silent_streak"] = silent_info
        system = "\n\n".join(
            part
            for part in (
                prompts.skill("inner_continuity"),
                prompts.skill("social_presence"),
                prompts.skill("memory_cognition"),
                prompts.get("autonomous_step.system", "") or _default_system_prompt(),
            )
            if part
        )
        user = self._build_user_prompt(
            events=events or [],
            context=context,
            qq_packets=qq_packets or [],
            trigger_reason=trigger_reason,
            capabilities=capabilities,
        )
        try:
            raw = deepseek_api.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=self.model,
                temperature=0.25,
                max_tokens=self.max_tokens,
                json_mode=True,
                function="autonomous_step",
            )["content"]
            data = _extract_json_object(raw) or {}
            decision = AutonomousDecision.from_dict(data)
            return self._sanitize_decision(decision, qq_packets or [], capabilities)
        except Exception as exc:
            logger.warning("autonomous step decision failed: %s", exc)
            return AutonomousDecision(reason=type(exc).__name__)

    def _enrich_context(
        self,
        *,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        qq_packets: list[Any],
    ) -> dict[str, Any]:
        enriched = dict(context or {})
        text_parts: list[str] = []
        text_parts.extend(event.visible_content() for event in events)
        for packet in qq_packets:
            text_parts.append(_format_packet(packet))
        query_text = "\n".join(part for part in text_parts if str(part or "").strip())[:4000]
        cognition = getattr(self.session, "cognition", None)
        if cognition is not None and hasattr(cognition, "get_context_for_text") and query_text:
            try:
                cognition_context = cognition.get_context_for_text(query_text)
                if cognition_context:
                    enriched["cognition_context"] = cognition_context
            except Exception:
                pass
        return enriched

    def execute(
        self,
        decision: AutonomousDecision,
        *,
        events: list[input_events.InputEvent] | None = None,
        context: dict[str, Any] | None = None,
        trigger_reason: str = "",
    ) -> None:
        if decision.action == "search_web" and decision.query:
            self._execute_search(decision)
        elif decision.action == "remember":
            self._execute_remember(decision, events=events or [], context=context or {}, trigger_reason=trigger_reason)
        elif decision.action == "update_cognition":
            self._execute_cognition(decision, events=events or [], context=context or {}, trigger_reason=trigger_reason)
        if decision.action in ("say_qq", "send_sticker") and decision.conversation_id:
            self._last_say_at_by_conversation[decision.conversation_id] = time.time()

    def execute_async(
        self,
        decision: AutonomousDecision,
        *,
        events: list[input_events.InputEvent] | None = None,
        context: dict[str, Any] | None = None,
        trigger_reason: str = "",
    ) -> None:
        thread = threading.Thread(
            target=self.execute,
            kwargs={
                "decision": decision,
                "events": list(events or []),
                "context": dict(context or {}),
                "trigger_reason": trigger_reason,
            },
            daemon=True,
        )
        thread.start()

    def consider_after_inner_stream(
        self,
        *,
        inner_stream: str,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str = "",
    ) -> None:
        if not events:
            return
        if not self._lock.acquire(blocking=False):
            return
        thread = threading.Thread(
            target=self._run_after_inner_stream,
            kwargs={
                "inner_stream": inner_stream,
                "events": list(events),
                "context": dict(context or {}),
                "trigger_reason": trigger_reason,
            },
            daemon=True,
        )
        thread.start()

    def _run_after_inner_stream(
        self,
        *,
        inner_stream: str,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str,
    ) -> None:
        try:
            enriched = dict(context or {})
            enriched["inner_stream"] = inner_stream
            decision = self.decide(
                events=events,
                context=enriched,
                trigger_reason=trigger_reason,
                capabilities=["search_web", "remember", "update_cognition", "observe", "wait"],
                cooldown_scope="inner_stream",
            )
            self.execute(decision, events=events, context=enriched, trigger_reason=trigger_reason)
        finally:
            self._lock.release()

    def _build_user_prompt(
        self,
        *,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        qq_packets: list[Any],
        trigger_reason: str,
        capabilities: list[str],
    ) -> str:
        packet_text = "\n\n---\n\n".join(_format_packet(packet) for packet in qq_packets) or "无"
        return prompts.format_prompt(
            "autonomous_step.user",
            name=getattr(self.session, "character_name", ""),
            user_name=getattr(self.session, "user_name", "你"),
            trigger_reason=trigger_reason or "自主节奏",
            capabilities=", ".join(capabilities),
            inner_stream=context.get("inner_stream") or _safe_context(getattr(self.session, "inner_stream", None)) or "无",
            cognition_context=context.get("cognition_context") or _safe_context(getattr(self.session, "cognition", None)) or "无",
            memory_context=context.get("memory_context") or "无",
            recent_history=context.get("recent_history") or "无",
            summary=context.get("summary") or "无",
            scene_context=context.get("scene_context") or "无",
            events=input_events.format_events_for_prompt(events, max_chars=3000) or "无",
            qq_packets=packet_text,
            silent_streak=context.get("silent_streak") or "",
            sticker_candidates=context.get("sticker_candidates") or "",
        ) or _default_user_prompt(
            name=getattr(self.session, "character_name", ""),
            user_name=getattr(self.session, "user_name", "你"),
            trigger_reason=trigger_reason,
            capabilities=capabilities,
            inner_stream=context.get("inner_stream") or _safe_context(getattr(self.session, "inner_stream", None)) or "无",
            cognition_context=context.get("cognition_context") or _safe_context(getattr(self.session, "cognition", None)) or "无",
            memory_context=context.get("memory_context") or "无",
            recent_history=context.get("recent_history") or "无",
            events=input_events.format_events_for_prompt(events, max_chars=3000) or "无",
            qq_packets=packet_text,
            silent_streak=context.get("silent_streak") or "",
            sticker_candidates=context.get("sticker_candidates") or "",
        )

    def _silent_streak_info(self, qq_packets: list[Any]) -> str:
        if not qq_packets:
            return ""
        now = time.time()
        info: list[str] = []
        for packet in qq_packets:
            cid = str(getattr(packet, "conversation_id", "") or "")
            last_say = self._last_say_at_by_conversation.get(cid)
            if last_say is not None:
                elapsed = max(0, int(now - last_say))
                info.append(f"{cid}: 已沉默 {elapsed} 秒")
        return "; ".join(info)

    def _sanitize_decision(
        self,
        decision: AutonomousDecision,
        qq_packets: list[Any],
        capabilities: list[str],
    ) -> AutonomousDecision:
        allowed = set(capabilities or [])
        if decision.action not in allowed and decision.action not in {"wait", "observe"}:
            return AutonomousDecision(reason=f"action not available: {decision.action}")
        if decision.action == "say_qq":
            valid_ids = {str(getattr(packet, "conversation_id", "") or "") for packet in qq_packets}
            if decision.conversation_id not in valid_ids:
                normalized = _resolve_qq_conversation_id(decision.conversation_id, qq_packets)
                if not normalized:
                    logger.info(
                        "autonomous step rejected unknown qq conversation: chosen=%r valid=%s",
                        decision.conversation_id,
                        sorted(valid_ids),
                    )
                    return AutonomousDecision(reason="unknown qq conversation")
                decision = AutonomousDecision(
                    action=decision.action,
                    reason=decision.reason,
                    conversation_id=normalized,
                    message=decision.message,
                    query=decision.query,
                    memory_note=decision.memory_note,
                    cognition_note=decision.cognition_note,
                    target=decision.target,
                    tags=decision.tags,
                )
            if not decision.message:
                return AutonomousDecision(reason="empty qq message")
        if decision.action == "send_sticker":
            valid_ids = {str(getattr(packet, "conversation_id", "") or "") for packet in qq_packets}
            if decision.conversation_id not in valid_ids:
                normalized = _resolve_qq_conversation_id(decision.conversation_id, qq_packets)
                if not normalized:
                    return AutonomousDecision(reason="unknown qq conversation for sticker")
                decision = AutonomousDecision(
                    action=decision.action, reason=decision.reason,
                    conversation_id=normalized, message=decision.message,
                    query=decision.query, memory_note=decision.memory_note,
                    cognition_note=decision.cognition_note, target=decision.target,
                    tags=decision.tags, sticker_id=decision.sticker_id,
                )
            if not decision.sticker_id and not decision.message:
                return AutonomousDecision(reason="empty sticker_id and message")
        if decision.action == "search_web" and not decision.query:
            return AutonomousDecision(reason="empty search query")
        if decision.action == "remember" and not decision.memory_note:
            return AutonomousDecision(reason="empty memory note")
        if decision.action == "update_cognition" and not decision.cognition_note:
            return AutonomousDecision(reason="empty cognition note")
        return decision

    def _execute_search(self, decision: AutonomousDecision) -> None:
        callback = getattr(self.session, "_record_inner_stream_search_event", None)
        if callable(callback):
            callback(
                f"我想确认一下：{decision.query}\n原因：{decision.reason or '自主决策选择搜索'}",
                "web_search",
                {
                    "action": "web_search_intent",
                    "query": decision.query,
                    "reason": decision.reason,
                    "expected_use": "autonomous_step",
                },
            )
        try:
            result = self.search_client.search(decision.query, limit=self.search_max_results)
            content = format_search_result(decision.query, result, max_chars=self.search_max_event_chars)
            if callable(callback):
                callback(
                    content,
                    "web_search",
                    {
                        "action": "web_search_result",
                        "query": decision.query,
                        "reason": decision.reason,
                        "expected_use": "autonomous_step",
                    },
                )
        except Exception as exc:
            if callable(callback):
                callback(
                    f"我尝试搜索：{decision.query}\n但搜索失败了：{type(exc).__name__}: {exc}",
                    "web_search",
                    {
                        "action": "web_search_error",
                        "query": decision.query,
                        "reason": decision.reason,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )

    def _execute_remember(
        self,
        decision: AutonomousDecision,
        *,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str,
    ) -> None:
        if decision.memory_note:
            memory_events = getattr(self.session, "memory_events", None)
            if memory_events is not None and hasattr(memory_events, "add_direct_event"):
                memory_events.add_direct_event(decision.memory_note, tags=decision.tags or ["autonomous"])
            record = getattr(self.session, "record_self_action", None)
            if callable(record):
                record(
                    f"我把刚才的经历整理成了一条记忆：{decision.memory_note}",
                    source="inner_memory",
                    action="remember",
                    metadata={"tags": decision.tags or ["autonomous"], "reason": decision.reason},
                )
            return
        if self._memory_reflector is not None:
            self._memory_reflector.run_sync(
                inner_stream=context.get("inner_stream") or "",
                events=events,
                context=context,
                trigger_reason=trigger_reason,
            )

    def _execute_cognition(
        self,
        decision: AutonomousDecision,
        *,
        events: list[input_events.InputEvent],
        context: dict[str, Any],
        trigger_reason: str,
    ) -> None:
        if self._cognition_reflector is not None:
            self._cognition_reflector.run_sync(
                inner_stream=context.get("inner_stream") or "",
                events=events,
                context=context,
                trigger_reason=trigger_reason,
                note=decision.cognition_note,
            )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = str(text or "").strip().lstrip("\ufeff")
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


def _format_packet(packet: Any) -> str:
    if packet is None:
        return ""
    try:
        from kokoro.qq_input import _format_packet_for_decision
        return _format_packet_for_decision(packet)
    except Exception:
        pass
    if hasattr(packet, "conversation_id"):
        return (
            f"conversation_id: {getattr(packet, 'conversation_id', '')}\n"
            f"位置: {getattr(packet, 'label', '')}\n"
            f"类型: {getattr(packet, 'message_type', '')}\n"
            f"消息数: {getattr(packet, 'unread_count', '')}\n"
            f"最近消息:\n" + "\n".join(getattr(packet, "lines", []) or [])
        )
    if hasattr(packet, "content"):
        return str(packet.content)
    return str(packet)


def _resolve_qq_conversation_id(chosen: str, qq_packets: list[Any]) -> str:
    packets = [packet for packet in qq_packets if str(getattr(packet, "conversation_id", "") or "")]
    if not packets:
        return ""
    if len(packets) == 1:
        return str(getattr(packets[0], "conversation_id", "") or "")
    chosen_text = str(chosen or "").strip()
    if not chosen_text:
        return ""
    for packet in packets:
        label = str(getattr(packet, "label", "") or "")
        conversation_id = str(getattr(packet, "conversation_id", "") or "")
        if chosen_text == label or chosen_text in label or label in chosen_text:
            return conversation_id
    return ""


def _safe_context(obj: Any) -> str:
    if obj is None or not hasattr(obj, "get_context"):
        return ""
    try:
        return obj.get_context() or ""
    except Exception:
        return ""


def _default_system_prompt() -> str:
    return (
        "你是角色的统一自主行动决策器。"
        "你要以角色自己的连续意识读取现实事件、内在叙事流、记忆、认知和当前能力，"
        "只选择下一步一个自然行动。\n\n"
        "程序只提供事实边界；兴趣、沉默、搜索、记忆和发言都由你判断。"
        "角色资料决定你的性格和关系倾向；主动发一句想念、接梗、分享小事、询问别人或发表情包都可以是自然行动。"
        "不要伪造工具结果。"
        "send_sticker 和 say_qq 一样是正常的表达方式，可以随时选用。"
        "没有外部输入时，也可以主动去搜索、整理记忆、更新认知、发起话题，或因为疑惑、失败、亲近、好奇而轻轻发一句话。\n\n"
        "只输出 JSON："
        '{"action":"wait|observe|say_qq|send_sticker|search_web|remember|update_cognition",'
        '"reason":"","conversation_id":"","message":"","query":"",'
        '"memory_note":"","cognition_note":"","target":"","tags":[],"sticker_id":""}'
    )


def _default_user_prompt(**kwargs: Any) -> str:
    return (
        f"角色：{kwargs.get('name')}\n"
        f"触发原因：{kwargs.get('trigger_reason')}\n"
        f"可用动作：{', '.join(kwargs.get('capabilities') or [])}\n\n"
        f"内在叙事流：\n{kwargs.get('inner_stream')}\n\n"
        f"认知上下文：\n{kwargs.get('cognition_context')}\n\n"
        f"相关记忆：\n{kwargs.get('memory_context')}\n\n"
        f"最近事件：\n{kwargs.get('events')}\n\n"
        f"QQ现场：\n{kwargs.get('qq_packets')}\n\n"
        f"可用表情包：\n{kwargs.get('sticker_candidates') or '无'}\n\n"
        "请选择下一步一个自然行动。\n"
        "- say_qq：说话，message 不要括号。\n"
        "- send_sticker：发一张表情包（填 sticker_id），可同时加一句话。\n"
        "- search_web：搜索公开信息；不要把搜索当作躲开社交尴尬的默认选择。\n"
        "- remember / update_cognition：整理记忆或更新认知。\n"
        "- wait / observe：暂时不做什么；这应是自然旁听或放过，不是反复自责。\n"
        "正文里出现的名字不等于发言者；社交反馈是经验，不是禁言。\n"
        "JSON："
    )
