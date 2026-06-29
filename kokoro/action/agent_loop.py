"""Agent loop: orchestrates LLM streaming + tool execution cycles.

When tools are configured, the loop:
  1. Sends messages + tool schemas to the LLM
  2. If LLM returns tool_calls: executes them, feeds results back, re-calls LLM
  3. If LLM returns text content: streams it to the caller
  4. Repeats until the final text response or max iterations reached

Without tools, this degenerates to a thin wrapper around llm_client.stream_chat().
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Callable, Optional

import requests

from kokoro.core import config as cfg
from kokoro.action import agent_guard
from kokoro.core import llm_client
from kokoro.action import model as action_model
from kokoro.core import input_events
from kokoro.action.tool_parser import (
    CompletedToolCall,
    ToolCallAccumulator,
    parse_sse_chunk,
)

logger = logging.getLogger(__name__)
_PAREN_STRIP_RE = re.compile(r"\s*[\uff08(][^\uff09)]*[\uff09)]\s*")


def _strip_parens(text: str) -> str:
    return _PAREN_STRIP_RE.sub("", text).strip()


class _ParenFilter:
    """Stateful filter to remove parenthetical content during streaming."""

    def __init__(self):
        self._depth = 0

    def filter(self, text: str) -> str:
        result: list[str] = []
        for ch in text:
            if ch in "\uff08(":
                self._depth += 1
            elif ch in "\uff09)":
                if self._depth > 0:
                    self._depth -= 1
            elif self._depth == 0:
                result.append(ch)
        return "".join(result)


class AgentConfig:
    def __init__(
        self,
        tools: list[dict] | None = None,
        tool_registry: object | None = None,
        max_tool_iterations: int = 5,
        tool_timeout: float = 45.0,
        on_tool_call: Optional[Callable[[str, dict], None]] = None,
        subtitle_client=None,
    ):
        self.tools = tools
        self.tool_registry = tool_registry
        self.max_tool_iterations = max_tool_iterations
        self.tool_timeout = tool_timeout
        self.on_tool_call = on_tool_call
        self.subtitle_client = subtitle_client


class AgentResult:
    def __init__(self, reply: str = "", cancelled: bool = False, tool_calls_made: int = 0):
        self.reply = reply
        self.cancelled = cancelled
        self.tool_calls_made = tool_calls_made

    def __iter__(self):
        """Support tuple unpacking: reply, cancelled = result"""
        return iter((self.reply, self.cancelled))


def agent_chat(
    messages: list[dict],
    model: str,
    agent_config: AgentConfig | None = None,
    cancel_event: threading.Event | None = None,
    tts_engine: object | None = None,
    character_config: dict | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    usage_callback=None,
    **tool_context,
) -> AgentResult:
    """Run the agent loop.

    Tool context kwargs are forwarded to tool_registry.execute():
      session, memory_backend, character_id

    Returns AgentResult with .reply, .cancelled, .tool_calls_made.
    Also supports tuple unpacking: reply, cancelled = result
    """
    _subtitle = getattr(agent_config, "subtitle_client", None) if agent_config else None
    capture = tool_context.pop("capture", False) if isinstance(tool_context, dict) else False
    if agent_config is None or not agent_config.tools or agent_config.tool_registry is None:
        reply, cancelled = _simple_stream(
            messages, model,
            cancel_event=cancel_event,
            tts_engine=tts_engine,
            character_config=character_config,
            api_base_url=api_base_url,
            api_key=api_key,
            usage_callback=usage_callback,
            subtitle_client=_subtitle,
            capture=capture,
        )
        return AgentResult(reply=reply, cancelled=cancelled, tool_calls_made=0)

    return _agent_chat_impl(
        messages=messages,
        model=model,
        tool_schemas=agent_config.tools,
        registry=agent_config.tool_registry,
        max_iter=agent_config.max_tool_iterations,
        timeout_val=agent_config.tool_timeout,
        on_tool_call=agent_config.on_tool_call,
        cancel_event=cancel_event,
        tts_engine=tts_engine,
        api_base_url=api_base_url,
        api_key=api_key,
        usage_callback=usage_callback,
        subtitle_client=_subtitle,
        **tool_context,
    )


def _agent_chat_impl(
    messages: list[dict],
    model: str,
    tool_schemas: list[dict],
    registry: object,
    max_iter: int,
    timeout_val: float,
    on_tool_call: Optional[Callable[[str, dict], None]],
    cancel_event: threading.Event | None,
    tts_engine: object | None,
    api_base_url: str | None,
    api_key: str | None,
    usage_callback=None,
    subtitle_client=None,
    **tool_context,
) -> AgentResult:
    """Stream LLM response with agent loop using raw SSE parsing."""

    total_tool_calls = 0
    total_prompt = 0
    total_completion = 0
    final_reply = ""
    working_messages = list(messages)
    paren_filter = _ParenFilter()

    available_tool_names = [
        str(t.get("function", {}).get("name") or "")
        for t in tool_schemas
        if t.get("function", {}).get("name")
    ]
    cycle_id = f"agent_{int(time.time() * 1000)}"
    causality_id = action_model.new_causality_id()
    router_messages = list(working_messages)
    task_manager = tool_context.get("task_manager")
    if task_manager is not None and hasattr(task_manager, "list_active"):
        try:
            active_tasks = task_manager.list_active()
        except Exception:
            active_tasks = []
        if active_tasks:
            router_messages.append({
                "role": "system",
                "content": (
                    "【当前正在执行的智能体任务】\n"
                    + "\n".join(t.to_prompt_line() for t in active_tasks[:5])
                    + "\n如果用户只是在催促、表达着急、询问好了没有、问为什么有多个任务，"
                    "应优先调用 check_task_progress，不要重复启动相同任务。"
                ),
            })
    route_model = cfg.tool_router_model() or model
    route = agent_guard.AgentRouter(
        model=route_model,
        api_base_url=api_base_url,
        api_key=api_key,
    ).decide(router_messages, available_tool_names)
    print(
        f"  [agent-router] call_tool={route.call_tool} "
        f"tool={route.tool_name or '-'} model={route_model} reason={route.reason or '-'}"
    )
    def _execute_routed_tool(route_to_execute: agent_guard.AgentRouteDecision) -> AgentResult:
        args = dict(route_to_execute.arguments)
        if route_to_execute.tool_name == "claude_code_exec" and args.get("task"):
            args["task"] = _enrich_agent_task(str(args["task"]), messages)
            route_to_execute.task = args["task"]
        if on_tool_call:
            on_tool_call(route_to_execute.tool_name, args)
        print(f"\n  [tool] {route_to_execute.tool_name} {json.dumps(args, ensure_ascii=False)[:240]}")
        t0 = time.perf_counter()
        result = registry.execute(route_to_execute.tool_name, args, **tool_context)
        elapsed = time.perf_counter() - t0
        _publish_tool_action_result(
            tool_context,
            tool_name=route_to_execute.tool_name,
            arguments=args,
            result=str(result),
            status=_tool_result_status(str(result)),
            elapsed=elapsed,
            cycle_id=cycle_id,
            causality_id=causality_id,
        )
        print(f"  [tool] {route_to_execute.tool_name} done ({elapsed:.1f}s)")
        if result:
            print(f"  [tool] {route_to_execute.tool_name} result={result[:160]}")
        result_text = str(result)
        is_pending_result = any(marker in result_text for marker in ("后台处理中", "任务已创建", "pending", "running"))
        is_failed_result = any(marker in result_text.lower() for marker in ("failed", "失败", "错误", "error", "退出码"))
        followup_messages = list(messages)
        followup_messages.append({
            "role": "system",
            "content": (
                "【智能体调用结果】\n"
                f"你刚刚已经通过自己的智能体能力调用了 {route_to_execute.tool_name}。"
                "这不是别人替你做事，也不是传话；这是你通过系统拥有的真实电脑操作能力的一部分。"
                "你应该把它理解为自己能够做的事：决定、执行、等待结果、核对结果，然后对用户负责。\n"
                f"任务描述：{route_to_execute.task}\n"
                f"工具返回：{result}\n\n"
                "请基于这个事实自然回应用户。不要说自己只是传话或让别人去做；"
                + (
                    "强约束：工具返回显示任务失败或出错。你必须承认没有完成，并简短说明错误；"
                    "禁止说已经完成、已经创建、已经保存、弄好了。\n"
                    if is_failed_result else ""
                )
                + (
                    "强约束：工具只返回了任务已创建/后台处理中，任务还没有完成。"
                    "你必须说正在做、正在处理或等完成后确认，禁止说已经完成、已经创建、已经保存、弄好了。\n"
                    if is_pending_result else ""
                )
                + "如果工具返回只是任务已创建/后台处理中，就只能说正在处理或正在查看进度；"
                "不要声称任务已经最终完成，除非工具返回明确说明 completed 或成功结果。"
            ),
        })
        reply, cancelled = _simple_stream(
            followup_messages,
            model,
            cancel_event=cancel_event,
            tts_engine=tts_engine,
            api_base_url=api_base_url,
            api_key=api_key,
            usage_callback=usage_callback,
            subtitle_client=subtitle_client,
        )
        return AgentResult(reply=reply, cancelled=cancelled, tool_calls_made=1)

    if route.call_tool:
        return _execute_routed_tool(route)

    for iteration in range(max_iter):
        accumulator = ToolCallAccumulator()
        iteration_reply = ""
        had_tool_calls = False
        pending_completed: list[CompletedToolCall] = []

        base_url = api_base_url.rstrip("/") if api_base_url else llm_client.api_base_for(model)
        if not re.search(r"/v\d+$", base_url):
            base_url += "/v1"
        headers = llm_client.api_headers(model)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = llm_client.build_payload(model, working_messages, stream=True, tools=tool_schemas)
        print(f"\n  [agent] iteration={iteration}, tools_in_payload={'tools' in payload and bool(payload['tools'])}")
        if payload.get("tools"):
            print(f"  [agent] tool_names={[t.get('function',{}).get('name','?') for t in payload['tools']]}")
        print(f"  [agent] messages={len(working_messages)} (system={sum(1 for m in working_messages if m['role']=='system')}, hist={sum(1 for m in working_messages if m['role']!='system')})")

        resp = requests.post(
            f"{base_url}/chat/completions",
            json=llm_client.build_payload(model, working_messages, stream=True, tools=tool_schemas),
            headers=headers,
            stream=True,
            timeout=120,
        )
        if not resp.ok:
            raise RuntimeError(f"API error {resp.status_code}: {resp.text[:200]}")

        resp.encoding = "utf-8"
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if cancel_event and cancel_event.is_set():
                    if usage_callback and (total_prompt or total_completion):
                        usage_callback({"prompt_tokens": total_prompt, "completion_tokens": total_completion})
                    resp.close()
                    return AgentResult(reply=final_reply + iteration_reply, cancelled=True, tool_calls_made=total_tool_calls)

                usage = llm_client.parse_sse_usage(line)
                if usage:
                    total_prompt += int(usage.get("prompt_tokens", 0))
                    total_completion += int(usage.get("completion_tokens", 0))

                chunk = parse_sse_chunk(line)

                if chunk.content:
                    content = paren_filter.filter(chunk.content)
                    if not content:
                        continue
                    iteration_reply += content

                if chunk.tool_call_deltas:
                    had_tool_calls = True
                    completed = accumulator.feed(chunk.tool_call_deltas)
                    pending_completed.extend(completed)

                if chunk.finish_reason in ("stop", "tool_calls"):
                    break
        finally:
            if cancel_event and cancel_event.is_set():
                resp.close()

        final_reply += iteration_reply

        if not had_tool_calls or not pending_completed:
            finish_reason = chunk.finish_reason if chunk else "no_chunk"
            print(f"  [agent] no tool calls (finish={finish_reason}, had_tool_calls={had_tool_calls}, pending={len(pending_completed)})")
            if iteration_reply:
                print(f"  [agent] text_reply={iteration_reply[:80]}")
            if total_tool_calls == 0 and iteration_reply:
                audit = agent_guard.AgentRouter(
                    model=route_model,
                    api_base_url=api_base_url,
                    api_key=api_key,
                ).audit_reply(working_messages, iteration_reply, total_tool_calls, available_tool_names)
                print(
                    f"  [agent-audit] call_tool={audit.call_tool} "
                    f"tool={audit.tool_name or '-'} reason={audit.reason or '-'}"
                )
                if audit.call_tool:
                    return _execute_routed_tool(audit)
            if iteration_reply:
                print(iteration_reply, end="", flush=True)
                if tts_engine:
                    tts_engine.push(iteration_reply)
                if subtitle_client:
                    subtitle_client.push_text(iteration_reply, mode="append")
            if usage_callback and (total_prompt or total_completion):
                usage_callback({"prompt_tokens": total_prompt, "completion_tokens": total_completion})
            return AgentResult(reply=final_reply, cancelled=False, tool_calls_made=total_tool_calls)

        print(f"  [agent] TOOL CALLS: {len(pending_completed)}")
        for tc in pending_completed:
            print(f"  [agent]   -> {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)[:120]})")
        # Build assistant message with tool_calls
        assistant_tool_calls = []
        for tc in pending_completed:
            assistant_tool_calls.append({
                "id": tc.call_id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            })

        working_messages.append({
            "role": "assistant",
            "content": iteration_reply or None,
            "tool_calls": assistant_tool_calls,
        })

        # Execute tools and append results
        for tc in pending_completed:
            if on_tool_call:
                on_tool_call(tc.name, tc.arguments)
            print(f"\n  [tool] {tc.name} {json.dumps(tc.arguments, ensure_ascii=False)}")
            t0 = time.perf_counter()

            result = registry.execute(tc.name, tc.arguments, **tool_context)

            elapsed = time.perf_counter() - t0
            _publish_tool_action_result(
                tool_context,
                tool_name=tc.name,
                arguments=tc.arguments,
                result=str(result),
                status=_tool_result_status(str(result)),
                elapsed=elapsed,
                cycle_id=cycle_id,
                causality_id=causality_id,
            )
            print(f"  [tool] {tc.name} done ({elapsed:.1f}s)")
            total_tool_calls += 1

            working_messages.append({
                "role": "tool",
                "tool_call_id": tc.call_id,
                "content": result,
            })

    if usage_callback and (total_prompt or total_completion):
        usage_callback({"prompt_tokens": total_prompt, "completion_tokens": total_completion})
    return AgentResult(reply=final_reply, cancelled=False, tool_calls_made=total_tool_calls)


def _enrich_agent_task(task: str, messages: list[dict]) -> str:
    recent = []
    for msg in messages:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        speaker = "用户" if role == "user" else "角色"
        recent.append(f"{speaker}: {content[:800]}")
    recent_text = "\n".join(recent[-12:])
    return (
        f"{task}\n\n"
        "执行时请参考下面最近对话。如果任务要求写入聊天记录、整理刚才内容、继续处理“这个文件”等，"
        "必须从这里提取实际内容和目标文件，不要把占位符或函数名当成任务本身。\n"
        f"【最近对话】\n{recent_text}"
    )


def _publish_tool_action_result(
    tool_context: dict,
    *,
    tool_name: str,
    arguments: dict,
    result: str,
    status: str,
    elapsed: float,
    cycle_id: str,
    causality_id: str,
) -> None:
    session = tool_context.get("session")
    bus = getattr(session, "event_bus", None)
    if bus is None or not hasattr(bus, "publish"):
        return
    event = input_events.build_action_result_event(
        f"工具 {tool_name} 返回：{result}",
        source=tool_name,
        metadata={
            "cycle_id": cycle_id,
            "action_id": f"tool_{tool_name}",
            "causality_id": causality_id,
            "action": tool_name,
            "arguments": arguments,
            "status": status,
            "elapsed_seconds": round(max(0.0, elapsed), 3),
            "mode": "sync",
            "visibility": "private",
            "result_policy": "feed_back",
        },
        priority="normal",
        lifetime="session",
    )
    bus.publish(event)


def _tool_result_status(result: str) -> str:
    text = str(result or "").lower()
    if any(marker in text for marker in ("failed", "失败", "错误", "error", "退出码", "超时", "timeout")):
        return "failed"
    if any(marker in text for marker in ("后台处理中", "任务已创建", "pending", "running")):
        return "pending"
    return "success"


def _simple_stream(
    messages: list[dict],
    model: str,
    cancel_event: threading.Event | None = None,
    tts_engine: object | None = None,
    character_config: dict | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    usage_callback=None,
    subtitle_client=None,
    capture: bool = False,
) -> tuple[str, bool]:
    """Fallback: plain streaming without tools.
    If capture=True, suppress printing and TTS (caller handles both)."""
    reply = ""
    paren_filter = _ParenFilter()
    for content in llm_client.stream_chat(
        messages, model,
        cancel_event=cancel_event,
        api_base_url=api_base_url,
        api_key=api_key,
        usage_callback=usage_callback,
    ):
        if cancel_event and cancel_event.is_set():
            return reply, True
        content = paren_filter.filter(content)
        if not content:
            continue
        if not capture:
            print(content, end="", flush=True)
        reply += content
        if tts_engine and not capture:
            tts_engine.push(content)
        if subtitle_client:
            subtitle_client.push_text(content, mode="append")
    return _strip_parens(reply), False
