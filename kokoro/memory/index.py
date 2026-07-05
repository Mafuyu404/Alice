"""Optional vector index adapter for Alice memory records."""

from __future__ import annotations

import threading


class MemoryIndex:
    """Sync prepared memory records to an external vector backend.

    The vector backend is an index only. It does not own memory truth,
    namespaces, consolidation, or recall expansion.
    """

    def __init__(self, *, character_id: str, vector_backend: object | None = None) -> None:
        self.character_id = character_id
        self.vector_backend = vector_backend

    @property
    def ready(self) -> bool:
        backend = self.vector_backend
        return bool(backend is not None and getattr(backend, "ready", False))

    def sync_record(self, *, content: str, summary: str = "", tags: list[str] | None = None) -> None:
        thread = threading.Thread(
            target=self.sync_record_now,
            kwargs={"content": content, "summary": summary, "tags": tags},
            daemon=True,
            name=f"memory-index-sync-{self.character_id}",
        )
        thread.start()

    def sync_record_now(self, *, content: str, summary: str = "", tags: list[str] | None = None) -> None:
        backend = self.vector_backend
        if backend is None or not getattr(backend, "ready", False):
            return
        try:
            text = str(content or "").strip()
            if not text:
                return
            if tags:
                text += "\n" + " ".join(f"#{tag}" for tag in tags)
            backend.store(text, summary or "memory_record", user_id=self.character_id)
        except Exception:
            return

    def context(self, query: str) -> str:
        backend = self.vector_backend
        if backend is None or not getattr(backend, "ready", False):
            return ""
        try:
            if hasattr(backend, "get_context"):
                return str(backend.get_context(query, user_id=self.character_id) or "").strip()
        except Exception:
            return ""
        return ""
