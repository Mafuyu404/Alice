"""Streaming LLM-to-speech helpers for the say tool."""

from __future__ import annotations

import re
import threading
import time

from kokoro.action import agent_loop
from kokoro.core import config as cfg
from kokoro.core import llm_client

_PAREN_STRIP_RE = re.compile(r"\s*[\uff08(][^\uff09)]*[\uff09)]\s*")


def strip_parens(text: str) -> str:
    return _PAREN_STRIP_RE.sub("", text).strip()


class ParenFilter:
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


def chat_stream(
    messages: list[dict],
    char_name: str,
    model: str,
    tts_engine: object | None,
    cancel_event: threading.Event | None = None,
    character_config: dict | None = None,
    agent_config: agent_loop.AgentConfig | None = None,
    tool_context: dict | None = None,
    usage_callback=None,
    subtitle_client=None,
    trace_t0: float | None = None,
    ai_context_callback=None,
) -> tuple[str, bool]:
    print(f"\n{char_name}: ", end="", flush=True)
    char_cfg = character_config or {}
    llm_api_base = char_cfg.get("llm_url") or None
    llm_api_key = cfg.charglm_api_key() or None if model.lower().startswith("charglm") else None

    if agent_config is not None:
        t0 = time.perf_counter()
        if trace_t0 is not None:
            print(f"\n  [trace] agent_request +{t0 - trace_t0:.2f}s model={model} messages={len(messages)}")
        result = agent_loop.agent_chat(
            messages,
            model,
            agent_config=agent_config,
            cancel_event=cancel_event,
            tts_engine=tts_engine,
            character_config=character_config,
            api_base_url=llm_api_base,
            api_key=llm_api_key,
            usage_callback=usage_callback,
            **(tool_context or {}),
        )
        if not result.cancelled:
            print()
            print(f"  [latency] llm_done {time.perf_counter() - t0:.2f}s")
            if trace_t0 is not None:
                print(
                    f"  [trace] agent_done +{time.perf_counter() - trace_t0:.2f}s "
                    f"reply={len(result.reply)}ch tools={result.tool_calls_made}"
                )
            if result.tool_calls_made > 0:
                print(f"  [tool] total tool calls: {result.tool_calls_made}")
        reply = strip_parens(result.reply)
        if reply and tts_engine and not result.cancelled:
            _end_tts_sentence(tts_engine)
        return reply, result.cancelled

    reply = ""
    t0 = time.perf_counter()
    if trace_t0 is not None:
        print(f"\n  [trace] llm_request +{t0 - trace_t0:.2f}s model={model} messages={len(messages)}")
    first_token_at = 0.0
    paren_filter = ParenFilter()
    cancelled = False

    for content in llm_client.stream_chat(
        messages,
        model,
        cancel_event=cancel_event,
        api_base_url=llm_api_base,
        api_key=llm_api_key,
        usage_callback=usage_callback,
    ):
        content = paren_filter.filter(content)
        if not content:
            continue
        if not first_token_at:
            first_token_at = time.perf_counter()
            print(f"\n  [latency] llm_first_token {first_token_at - t0:.2f}s")
            if trace_t0 is not None:
                print(f"  [trace] llm_first_token +{first_token_at - trace_t0:.2f}s")
            print(f"{char_name}: ", end="", flush=True)
        print(content, end="", flush=True)
        reply += content
        if ai_context_callback:
            ai_context_callback(reply)
        if tts_engine:
            tts_engine.push(content)
        if subtitle_client:
            subtitle_client.push_text(content, mode="append")

    if cancel_event and cancel_event.is_set():
        cancelled = True
        print(f"\n  [interrupt] barge-in, cancelled after {time.perf_counter() - t0:.1f}s")
    else:
        print()
    reply = strip_parens(reply)
    if reply and tts_engine and not cancelled:
        _end_tts_sentence(tts_engine)
    if not cancelled:
        print(f"  [latency] llm_done {time.perf_counter() - t0:.2f}s")
        if trace_t0 is not None:
            print(f"  [trace] llm_done +{time.perf_counter() - trace_t0:.2f}s reply={len(reply)}ch")
    return reply, cancelled


def _end_tts_sentence(tts_engine: object) -> None:
    try:
        tts_engine.end_sentence(wait=False)
    except TypeError:
        tts_engine.end_sentence()
