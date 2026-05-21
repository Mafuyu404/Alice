"""LLM 驱动的智能体/工具路由。"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from typing import Any

from kokoro import config as cfg
from kokoro import llm_client
from kokoro import token_usage

logger = logging.getLogger(__name__)


def _looks_like_qq_message_request(text: str) -> bool:
    compact = re.sub(r"[\s\W_]+", "", text or "").lower()
    if "qq" not in compact and "q" not in compact:
        return False
    return any(marker in text for marker in ("消息", "群", "聊天", "看", "收到", "发"))


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
        if not self._model:
            return AgentRouteDecision(reason="no_router_model")
        tools = available_tools
        if _looks_like_qq_message_request(_recent_dialogue(messages)) and "send_qq_message" in available_tools:
            tools = ["send_qq_message"]
        prompt = (
            "你是工具完整性审核器。只输出 JSON，不要回答用户。\n"
            "如果最新用户要求真实电脑/文件操作，而候选回复在没有真实工具结果时声称已经完成，返回 call_tool=true。\n"
            "如果候选回复是在询问或声明已有后台任务状态，优先使用 check_task_progress，不要重新启动 claude_code_exec。\n"
            "JSON 格式：{\"call_tool\": boolean, \"tool_name\": string, \"arguments\": object, \"reason\": string}。\n\n"
            f"可用工具：{', '.join(tools)}\n"
            f"最近对话：\n{_recent_dialogue(messages)}\n\n"
            f"tool_calls_made: {tool_calls_made}\n"
            f"候选回复：{reply}\n"
            "JSON："
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
        return (
            "你正在为角色自己的真实能力做路由判断。不要扮演角色，不要回答用户，只输出 JSON。\n"
            "这些工具不是外部助手，不是把请求转交给别人；它们是角色通过系统实际能做到的能力。\n"
            "当用户要求角色做需要改变、检查或验证电脑状态的事情时，判断角色现在是否应该使用自己的电脑操作能力。\n\n"
            "工具：\n"
            "- claude_code_exec：启动一个新的后台电脑任务，例如创建/写入文件、修改代码、运行命令、整理文档。\n"
            "- check_task_progress：查询已有后台智能体任务。用户问之前任务是否完成、进度如何、为什么还没好时用它，不要重复启动同一个任务。\n"
            "- list_active_tasks：列出当前活跃任务。\n\n"
            "判断规则：\n"
            "- 普通聊天、知识问答、背诵、解释、情绪回应：call_tool=false。\n"
            "- 用户要求角色真实操作电脑、编辑文件、创建成果、运行命令、检查本地状态或验证结果：调用 claude_code_exec。\n"
            "- 用户已经给出期望结果，比如文件名和位置、代码修改目标、要验证的结果，这已经是具体任务。\n"
            "- 用户在任务启动后追问状态：调用 check_task_progress；不知道 task_id 时 arguments 用空对象 {}。\n"
            "- 如果系统上下文显示已有任务正在执行，而用户只是催促、表达着急、问好了没有、问为什么多个任务：调用 check_task_progress，绝不能重复启动 claude_code_exec。\n"
            "- 如果最近角色说任务正在创建、正在处理、正在执行，而最新用户只是短追问“好了吧”“好了吗”“完成了吗”“还没好吗”：调用 check_task_progress。\n"
            "- 不要把自然语言里的“我做了”当成证据；只有工具结果能证明执行。\n"
            "- 如果信息不足，仍可调用 claude_code_exec，让执行器安全检查并报告缺少什么。\n\n"
            "JSON 格式：{\"call_tool\": boolean, \"tool_name\": string, \"arguments\": object, \"reason\": string}。\n"
            "调用 claude_code_exec 时，arguments 必须包含 {\"task\": \"...\"}。\n"
            "调用 check_task_progress 时，arguments 可以是 {}。\n\n"
            f"可用工具名：{', '.join(available_tools)}\n"
            f"最近对话：\n{_recent_dialogue(messages)}\n\n"
            "JSON："
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
        base_url = self._api_base_url.rstrip("/") if self._api_base_url else llm_client.api_base_for(self._model)
        if not re.search(r"/v\d+$", base_url):
            base_url += "/v1"
        headers = llm_client.api_headers(self._model)
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = llm_client.build_payload(self._model, [{"role": "user", "content": prompt}], stream=False)
        payload["temperature"] = 0
        payload["max_tokens"] = 256
        try:
            req_headers = {"Content-Type": "application/json"}
            req_headers.update(headers)
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=req_headers,
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read())
            message = data.get("choices", [{}])[0].get("message", {})
            usage_data = data.get("usage") or {}
            pt = int(usage_data.get("prompt_tokens", 0) or 0)
            ct = int(usage_data.get("completion_tokens", 0) or 0)
            if pt or ct:
                token_usage.record(self._model, "agent_tool_route", pt, ct)
            return str(message.get("content") or "").strip()
        except Exception as exc:
            logger.debug("agent router failed: %s", exc)
            return ""
