"""LLM-driven context compaction for dense runtime continuity."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from kokoro.core import input_events
from kokoro.core import lifecycle_debug
from kokoro.prompt import PromptContext, PromptManager
from kokoro.prompt.contracts import LIFE_CONTEXT_COMPACT_SCENE


LlmCall = Callable[[list[dict], dict], str]


@dataclass
class ContextCompactor:
    character_id: str
    root: Path
    llm_call: LlmCall | None = None
    max_chars: int = 8000
    prompt_manager: PromptManager | None = None

    def __post_init__(self) -> None:
        self.path = self.root / "characters" / self.character_id / "context"
        self.path.mkdir(parents=True, exist_ok=True)
        if self.prompt_manager is None:
            self.prompt_manager = PromptManager()

    def append_live(self, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        _append(self.path / "live_timeline.txt", text)
        lifecycle_debug.log("life.context.live_append", character_id=self.character_id, chars=len(text))

    def append_event(self, event: input_events.InputEvent, text: str) -> None:
        if event.metadata.get("suppress_feedback"):
            lifecycle_debug.log("life.context.event_suppressed", character_id=self.character_id, event=event)
            return
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

    def clear_pending_threads(self) -> None:
        (self.path / "pending_threads.txt").write_text("", encoding="utf-8")
        lifecycle_debug.log("life.context.pending_threads_clear", character_id=self.character_id)

    def clear_tool_results(self) -> None:
        (self.path / "tool_results_digest.txt").write_text("", encoding="utf-8")
        lifecycle_debug.log("life.context.tool_results_clear", character_id=self.character_id)

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
        implementation = "fallback"
        if self.llm_call is None:
            digest = _fallback_compact(
                previous=previous,
                live=live,
                time_context=time_context,
                pending_threads=pending,
                tool_results=tool_results,
            )
        else:
            implementation = "llm"
            messages = self.prompt_manager.render(
                LIFE_CONTEXT_COMPACT_SCENE,
                PromptContext(
                    scene=LIFE_CONTEXT_COMPACT_SCENE,
                    character_id=self.character_id,
                    values={
                        "time_context": time_context or "(none)",
                        "inner_stream": inner_stream or "(empty)",
                        "previous_digest": previous or "(none)",
                        "pending_threads": pending or "(none)",
                        "tool_results_digest": tool_results or "(none)",
                        "live_events": live or "(none)",
                    },
                ),
            )
            digest = self.llm_call(messages, {"function": "life_context_compact", "max_tokens": 512}).strip()
        digest = _clean_digest(digest)[-self.max_chars :].strip()
        digest_path.write_text(digest + "\n", encoding="utf-8")
        live_path.write_text("", encoding="utf-8")
        self._append_compaction_audit(
            implementation=implementation,
            previous=previous,
            live=live,
            pending=pending,
            tool_results=tool_results,
            time_context=time_context,
            inner_stream=inner_stream,
            digest=digest,
        )
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

    def _append_compaction_audit(
        self,
        *,
        implementation: str,
        previous: str,
        live: str,
        pending: str,
        tool_results: str,
        time_context: str,
        inner_stream: str,
        digest: str,
    ) -> None:
        record = {
            "type": "context_compaction",
            "character_id": self.character_id,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "implementation": implementation,
            "max_chars": self.max_chars,
            "input_chars": {
                "previous_digest": len(previous or ""),
                "live_events": len(live or ""),
                "pending_threads": len(pending or ""),
                "tool_results_digest": len(tool_results or ""),
                "time_context": len(time_context or ""),
                "inner_stream": len(inner_stream or ""),
            },
            "output_chars": {
                "recent_digest": len(digest or ""),
            },
            "paths": {
                "live_timeline": str(self.path / "live_timeline.txt"),
                "recent_digest": str(self.path / "recent_digest.txt"),
                "pending_threads": str(self.path / "pending_threads.txt"),
                "tool_results_digest": str(self.path / "tool_results_digest.txt"),
            },
        }
        _append_jsonl(self.path / "compaction_audit.jsonl", record)
        lifecycle_debug.log("life.context.compact_audit", character_id=self.character_id, record=record)


def _append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(text.strip() + "\n")


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


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
        line = re.sub(r"^\*\*(.*?)\*\*\s*:?", r"\1:", line)
        line = line.replace("**", "").strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _is_empty_marker(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).lower()
    return compact in {"(empty)", "empty", "none", "null", "n/a", "无", "空", "暂无", "没有"}
