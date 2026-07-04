"""LLM-driven context compaction for dense runtime continuity."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kokoro.core import lifecycle_debug
from kokoro.core import input_events
from kokoro.core import prompts


LlmCall = Callable[[list[dict], dict], str]


@dataclass
class ContextCompactor:
    character_id: str
    root: Path
    llm_call: LlmCall | None = None
    max_chars: int = 8000

    def __post_init__(self) -> None:
        self.path = self.root / "characters" / self.character_id / "context"
        self.path.mkdir(parents=True, exist_ok=True)

    def append_live(self, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        _append(self.path / "live_timeline.txt", text)
        lifecycle_debug.log("life.context.live_append", character_id=self.character_id, chars=len(text))

    def append_event(self, event: input_events.InputEvent, text: str) -> None:
        self.append_live(text)
        if event.type == "action_result":
            self.append_tool_result(text)

    def append_tool_result(self, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        path = self.path / "tool_results_digest.txt"
        existing = _read(path)
        merged = _clean_digest("\n".join(part for part in (existing, text) if part))[-self.max_chars :].strip()
        path.write_text(merged + "\n", encoding="utf-8")
        lifecycle_debug.log("life.context.tool_result_append", character_id=self.character_id, chars=len(text))

    def record_pending_threads(self, text: str) -> None:
        text = _clean_digest(text)
        if not text or _is_empty_marker(text):
            return
        (self.path / "pending_threads.txt").write_text(text[-self.max_chars :].strip() + "\n", encoding="utf-8")
        lifecycle_debug.log("life.context.pending_threads_write", character_id=self.character_id, chars=len(text))

    def compact_once(self, *, time_context: str, inner_stream: str) -> str:
        live_path = self.path / "live_timeline.txt"
        digest_path = self.path / "recent_digest.txt"
        live = _read(live_path)[-self.max_chars :]
        previous = _read(digest_path)[-self.max_chars :]
        pending = _read(self.path / "pending_threads.txt")[-self.max_chars :]
        tool_results = _read(self.path / "tool_results_digest.txt")[-self.max_chars :]
        if not live.strip():
            return previous
        lifecycle_debug.log(
            "life.context.compact_before",
            character_id=self.character_id,
            time_context=time_context,
            inner_stream=inner_stream,
            previous_digest=previous,
            pending_threads=pending,
            tool_results_digest=tool_results,
            live_events=live,
        )
        if self.llm_call is None:
            digest = _fallback_compact(
                previous=previous,
                live=live,
                time_context=time_context,
                pending_threads=pending,
                tool_results=tool_results,
            )
        else:
            messages = [
                {
                    "role": "system",
                    "content": prompts.get("life_runtime.context_compact_system", ""),
                },
                {
                    "role": "user",
                    "content": prompts.format_prompt(
                        "life_runtime.context_compact_user",
                        time_context=time_context or "无",
                        inner_stream=inner_stream or "空",
                        previous_digest=previous or "无",
                        pending_threads=pending or "无",
                        tool_results_digest=tool_results or "无",
                        live_events=live or "无",
                    ),
                },
            ]
            digest = self.llm_call(messages, {"function": "life_context_compact", "max_tokens": 512}).strip()
        digest = _clean_digest(digest)[-self.max_chars :].strip()
        digest_path.write_text(digest + "\n", encoding="utf-8")
        live_path.write_text("", encoding="utf-8")
        lifecycle_debug.log(
            "life.context.compact_done",
            character_id=self.character_id,
            chars=len(digest),
            digest=digest,
        )
        return digest

    def recent_digest(self) -> str:
        return _read(self.path / "recent_digest.txt")

    def pending_threads(self) -> str:
        return _read(self.path / "pending_threads.txt")

    def tool_results_digest(self) -> str:
        return _read(self.path / "tool_results_digest.txt")


def _append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(text.strip() + "\n")


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _fallback_compact(
    *,
    previous: str,
    live: str,
    time_context: str,
    pending_threads: str = "",
    tool_results: str = "",
) -> str:
    text = "\n".join(
        part
        for part in (
            previous.strip(),
            "[time]\n" + time_context.strip(),
            "[pending]\n" + pending_threads.strip() if pending_threads.strip() else "",
            "[tool_results]\n" + tool_results.strip() if tool_results.strip() else "",
            live.strip(),
        )
        if part
    )
    return text[-6000:]


def _clean_digest(text: str) -> str:
    cleaned = re.sub(r"```(?:text|txt|markdown|plaintext)?\s*\n?(.*?)```", r"\1", str(text or ""), flags=re.DOTALL)
    cleaned = re.sub(r"^(?:text|txt|markdown|plaintext)\s*\n+", "", cleaned.strip(), flags=re.IGNORECASE)
    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^\*\*(.*?)\*\*\s*:?", r"\1：", line)
        line = line.replace("**", "").strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _is_empty_marker(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).lower()
    return compact in {"(empty)", "empty", "none", "null", "n/a", "无", "空", "暂无", "没有"}
