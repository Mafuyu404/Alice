"""Model-maintained experience workspace between raw events and long memory."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kokoro.memory.models import now_iso


@dataclass
class MemoryWorkspaceState:
    current_experience: str = ""
    open_threads: str = ""
    recent_raw_digest: str = ""
    notes: str = ""
    updated_at: str = ""

    def as_context(self, *, max_chars: int = 12000) -> str:
        parts = [
            ("current_experience", self.current_experience),
            ("open_threads", self.open_threads),
            ("recent_raw_digest", self.recent_raw_digest),
        ]
        text = "\n\n".join(f"## {name}\n{value.strip()}" for name, value in parts if value.strip())
        if self.notes.strip():
            text += f"\n\n## workspace_notes\n{self.notes.strip()}"
        return text[-max_chars:]


class MemoryWorkspace:
    """Small file-backed workspace updated by prompts, not semantic code."""

    def __init__(self, *, character_id: str, root: Path) -> None:
        self.character_id = character_id
        self.root = Path(root)
        self.path = self.root / "characters" / character_id / "memory" / "workspace"
        self.path.mkdir(parents=True, exist_ok=True)
        self.current_experience_path = self.path / "current_experience.md"
        self.open_threads_path = self.path / "open_threads.md"
        self.recent_raw_digest_path = self.path / "recent_raw_digest.md"
        self.notes_path = self.path / "notes.md"
        self.meta_path = self.path / "workspace_meta.json"

    def read(self) -> MemoryWorkspaceState:
        return MemoryWorkspaceState(
            current_experience=self._read_text(self.current_experience_path),
            open_threads=self._read_text(self.open_threads_path),
            recent_raw_digest=self._read_text(self.recent_raw_digest_path),
            notes=self._read_text(self.notes_path),
            updated_at=str(self._read_meta().get("updated_at") or ""),
        )

    def write(self, state: MemoryWorkspaceState | dict[str, Any]) -> None:
        if isinstance(state, dict):
            state = MemoryWorkspaceState(
                current_experience=str(state.get("current_experience") or ""),
                open_threads=str(state.get("open_threads") or ""),
                recent_raw_digest=str(state.get("recent_raw_digest") or ""),
                notes=str(state.get("notes") or ""),
            )
        updated_at = now_iso()
        self.current_experience_path.write_text(state.current_experience.strip() + "\n", encoding="utf-8")
        self.open_threads_path.write_text(state.open_threads.strip() + "\n", encoding="utf-8")
        self.recent_raw_digest_path.write_text(state.recent_raw_digest.strip() + "\n", encoding="utf-8")
        self.notes_path.write_text(state.notes.strip() + "\n", encoding="utf-8")
        self.meta_path.write_text(
            json.dumps({"updated_at": updated_at}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def as_context(self, *, max_chars: int = 12000) -> str:
        return self.read().as_context(max_chars=max_chars)

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _read_meta(self) -> dict[str, Any]:
        try:
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}
