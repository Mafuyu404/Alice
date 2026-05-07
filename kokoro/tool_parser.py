"""Streaming tool-call delta parser for OpenAI-compatible SSE responses."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ToolCallDelta:
    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class CompletedToolCall:
    call_id: str
    name: str
    arguments: dict


@dataclass
class StreamChunk:
    content: str = ""
    tool_call_deltas: list[ToolCallDelta] = field(default_factory=list)
    finish_reason: str = ""


class ToolCallAccumulator:
    """Buffers incremental tool_call deltas and emits completed tool calls."""

    def __init__(self) -> None:
        self._slots: dict[int, dict] = {}

    def feed(self, deltas: list[ToolCallDelta]) -> list[CompletedToolCall]:
        completed: list[CompletedToolCall] = []
        for delta in deltas:
            slot = self._slots.setdefault(delta.index, {"id": "", "name": "", "args": ""})
            if delta.id:
                slot["id"] = delta.id
            if delta.name:
                slot["name"] = delta.name
            if delta.arguments:
                slot["args"] += delta.arguments
            call_id = slot["id"]
            name = slot["name"]
            args_str = slot["args"]
            if call_id and name and args_str:
                try:
                    arguments = json.loads(args_str)
                    completed.append(CompletedToolCall(call_id=call_id, name=name, arguments=arguments))
                except json.JSONDecodeError:
                    pass  # still accumulating
        return completed


def parse_sse_chunk(line: str) -> StreamChunk:
    """Parse an SSE line into a StreamChunk with content and/or tool_call deltas."""
    if not line or line == "data: [DONE]" or line.startswith(":"):
        if line == "data: [DONE]":
            return StreamChunk(finish_reason="stop")
        return StreamChunk()

    if not line.startswith("data: "):
        return StreamChunk()

    try:
        chunk = json.loads(line[6:])
    except json.JSONDecodeError:
        return StreamChunk()

    choice = chunk.get("choices", [{}])[0] or {}
    delta = choice.get("delta", {})
    finish = choice.get("finish_reason", "")

    content = delta.get("content") or ""

    tool_call_deltas: list[ToolCallDelta] = []
    raw_tool_calls = delta.get("tool_calls") or []
    for tc in raw_tool_calls:
        index = tc.get("index", 0)
        tc_id = tc.get("id") or ""
        func = tc.get("function") or {}
        name = func.get("name") or ""
        args = func.get("arguments") or ""
        tool_call_deltas.append(ToolCallDelta(index=index, id=tc_id, name=name, arguments=args))

    return StreamChunk(content=content, tool_call_deltas=tool_call_deltas, finish_reason=finish)
