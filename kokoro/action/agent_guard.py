"""LLM 驱动的智能体/工具路由。"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from typing import Any

from kokoro.core import config as cfg
from kokoro.core import llm_client
from kokoro.core import prompts
from kokoro.core import token_usage
from kokoro.action.tools import qq as qq_tool
from kokoro.action.tools import vts as vts_tool

logger = logging.getLogger(__name__)


def _looks_like_qq_message_request(text: str) -> bool:
    return qq_tool.looks_like_message_request(text)


def _direct_vts_route(text: str, available_tools: list[str]) -> AgentRouteDecision | None:
    route = vts_tool.direct_route(text, available_tools)
    if route is None:
        return None
    return AgentRouteDecision(
        True,
        str(route.get("tool_name") or ""),
        reason=str(route.get("reason") or ""),
        arguments=dict(route.get("arguments") or {}),
    )


def _text(message: dict) -> str:
    return str(message.get("content") or "").strip()


def _recent_dialogue(messages: list[dict], max_messages: int = 10) -> str:
    recent = [
        msg for msg in messages
        if msg.get("role") in ("user", "assistant", "system") and _text(msg)
    ][-max_messages:]
    lines: list[str] = []
    for msg in recent:
        role = msg.get("role")
        label = "用户" if role == "user" else ("角色" if role == "assistant" else "系统")
        lines.append(f"{label}: {_text(msg)[:700]}")
    return "\n".join(lines)


def _latest_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user" and _text(msg):
            return _text(msg)
    return ""


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = (text or "").strip()
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
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


class AgentRouteDecision:
    def __init__(
        self,
        call_tool: bool = False,
        tool_name: str = "",
        task: str = "",
        reason: str = "",
        arguments: dict[str, Any] | None = None,
    ):
        self.call_tool = call_tool
        self.tool_name = tool_name
        self.task = task
        self.reason = reason
        self.arguments = arguments or ({"task": task} if task else {})


class AgentRouter:
    """让 LLM 判断当前回合是否应该启动或查询智能体工具。"""

    def __init__(
        self,
        model: str | None = None,
        api_base_url: str | None = None,
        api_key: str | None = None,
    ):
        self._model = model or cfg.tool_router_model()
        self._api_base_url = api_base_url
        self._api_key = api_key

    def decide(self, messages: list[dict], available_tools: list[str]) -> AgentRouteDecision:
        direct_vts = _direct_vts_route(_latest_user_text(messages), available_tools)
        if direct_vts:
            return direct_vts
        if not self._model:
            return AgentRouteDecision(reason="no_router_model")
        tools = available_tools
        if _looks_like_qq_message_request(_recent_dialogue(messages)) and "send_qq_message" in available_tools:
            tools = ["send_qq_message"]
        return self._decide_from_prompt(self._build_prompt(messages, tools), tools, "invalid_json")

    def audit_reply(
        self,
        messages: list[dict],
        reply: str,
        tool_calls_made: int,
        available_tools: list[str],
    ) -> AgentRouteDecision:
        direct_vts = _direct_vts_route(_latest_user_text(messages), available_tools)
        if direct_vts:
            return direct_vts
        if not self._model:
            return AgentRouteDecision(reason="no_router_model")
        tools = available_tools
        if _looks_like_qq_message_request(_recent_dialogue(messages)) and "send_qq_message" in available_tools:
            tools = ["send_qq_message"]
        prompt = prompts.format_prompt(
            "agent_guard.audit_reply",
            available_tools=", ".join(tools),
            recent_dialogue=_recent_dialogue(messages),
            tool_calls_made=tool_calls_made,
            reply=reply,
        )
        return self._decide_from_prompt(prompt, tools, "audit_invalid_json")

    def _decision_from_json(self, data: dict[str, Any], available_tools: list[str]) -> AgentRouteDecision:
        call_tool = bool(data.get("call_tool", False))
        tool_name = str(data.get("tool_name") or "").strip()
        args = data.get("arguments")
        if not isinstance(args, dict):
            args = {}
        task = str(data.get("task") or args.get("task") or "").strip()
        reason = str(data.get("reason") or "").strip()
        if task and "task" not in args:
            args["task"] = task
        if not call_tool:
            return AgentRouteDecision(False, reason=reason or "no_tool")
        if tool_name not in available_tools:
            return AgentRouteDecision(False, reason=reason or "invalid_tool_request")
        if tool_name == "claude_code_exec" and not task:
            return AgentRouteDecision(False, reason=reason or "missing_task")
        return AgentRouteDecision(True, tool_name, task, reason, args)

    def _build_prompt(self, messages: list[dict], available_tools: list[str]) -> str:
        return prompts.format_prompt(
            "agent_guard.route",
            available_tools=", ".join(available_tools),
            recent_dialogue=_recent_dialogue(messages),
        )

    def _decide_from_prompt(
        self,
        prompt: str,
        available_tools: list[str],
        invalid_reason: str,
    ) -> AgentRouteDecision:
        raw = self._call_model(prompt)
        data = _extract_json_object(raw)
        if not data:
            logger.debug("agent router returned non-json: %r", raw[:300])
            return AgentRouteDecision(reason=invalid_reason)
        return self._decision_from_json(data, available_tools)

    def _call_model(self, prompt: str) -> str:
        from kokoro.core import deepseek_api

        try:
            return deepseek_api.chat(
                [{"role": "user", "content": prompt}],
                model=self._model,
                temperature=0,
                max_tokens=256,
                function="agent_tool_route",
            )["content"]
        except Exception as exc:
            logger.debug("agent router failed: %s", exc)
            return ""

