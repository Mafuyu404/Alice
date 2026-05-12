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

from kokoro import config as cfg
from kokoro import llm_client
from kokoro.tool_parser import (
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
                    print(content, end="", flush=True)
                    iteration_reply += content
                    if tts_engine:
                        tts_engine.push(content)
                    if subtitle_client:
                        subtitle_client.push_text(content, mode="append")

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
            if usage_callback and (total_prompt or total_completion):
                usage_callback({"prompt_tokens": total_prompt, "completion_tokens": total_completion})
            return AgentResult(reply=final_reply, cancelled=False, tool_calls_made=total_tool_calls)

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
