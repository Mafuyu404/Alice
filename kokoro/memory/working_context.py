"""High-density recent memory context files for the life loop."""

from __future__ import annotations

from pathlib import Path

from kokoro.memory.models import now_iso


class MemoryWorkingContext:
    """Small text files that help the LLM keep recent memory continuity.

    These files are not decision state. They are compact reading material for
    prompts: what was just remembered, recalled, or changed recently.
    """

    def __init__(self, *, character_id: str, root: Path) -> None:
        self.character_id = character_id
        self.root = Path(root)
        self.path = self.root / "characters" / character_id / "context"
        self.path.mkdir(parents=True, exist_ok=True)

    @property
    def recent_memory_digest_path(self) -> Path:
        return self.path / "recent_memory_digest.txt"

    def read_recent_memory_digest(self, *, max_chars: int = 2400) -> str:
        try:
            text = self.recent_memory_digest_path.read_text(encoding="utf-8")
        except OSError:
            return ""
        return text[-max(1, int(max_chars)) :].strip()

    def append_recent_memory(self, text: str, *, source: str = "memory") -> None:
        text = str(text or "").strip()
        if not text:
            return
        current = self.read_recent_memory_digest(max_chars=3200)
        normalized = " ".join(text.split())
        recent_lines = current.splitlines()[-12:]
        for line in recent_lines:
            existing = line.split("] ", 1)[-1]
            if " ".join(existing.split()) == f"{source}: {normalized}":
                return
        line = f"[{now_iso()}] {source}: {text}"
        updated = "\n".join(part for part in (current, line) if part).strip()
        self.recent_memory_digest_path.write_text(updated[-3200:] + "\n", encoding="utf-8")
