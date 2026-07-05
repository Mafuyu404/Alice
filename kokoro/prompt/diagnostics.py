"""Prompt diagnostics and trace export."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kokoro.prompt.fragment import RenderedFragment


@dataclass
class PromptTrace:
    scene: str
    context: dict[str, Any]
    snapshots_before: dict[str, Any] = field(default_factory=dict)
    snapshots_after: dict[str, Any] = field(default_factory=dict)
    fragments: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, str]] = field(default_factory=list)

    def add_fragment(self, fragment: RenderedFragment) -> None:
        self.fragments.append(
            {
                "id": fragment.id,
                "role": fragment.role,
                "scope": fragment.scope,
                "priority": fragment.priority,
                "char_count": fragment.char_count,
                "skipped_by_diff": fragment.skipped_by_diff,
                "truncated": fragment.truncated,
            }
        )

    def write(self, directory: str | Path) -> None:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        payload = {
            "scene": self.scene,
            "context": self.context,
            "snapshots_before": self.snapshots_before,
            "snapshots_after": self.snapshots_after,
            "fragments": self.fragments,
            "messages": self.messages,
        }
        (path / "trace.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (path / "context.json").write_text(
            json.dumps(self.context, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (path / "selected_fragments.json").write_text(
            json.dumps(self.fragments, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (path / "snapshots_before.json").write_text(
            json.dumps(self.snapshots_before, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (path / "snapshots_after.json").write_text(
            json.dumps(self.snapshots_after, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (path / "messages.json").write_text(
            json.dumps(self.messages, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        for role in ("system", "developer", "user"):
            content = "\n\n".join(message["content"] for message in self.messages if message["role"] == role)
            if content:
                (path / f"rendered_{role}.md").write_text(content, encoding="utf-8")
