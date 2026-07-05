"""Model-visible life context fragments.

These fragments are runtime provenance wrappers. They do not classify meaning
or decide importance; they only keep source, time, and size boundaries visible
to the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class LifeContextFragment:
    source: str
    content: str
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    max_chars: int = 4000

    def render(self) -> str:
        source = _safe_attr(self.source or "runtime")
        created_at = _safe_attr(self.created_at)
        max_chars = max(200, int(self.max_chars or 4000))
        content = str(self.content or "").strip()
        if len(content) > max_chars:
            content = content[-max_chars:].strip()
        if not content:
            content = "(none)"
        return (
            f'<life_context source="{source}" created_at="{created_at}" max_chars="{max_chars}">\n'
            f"{content}\n"
            "</life_context>"
        )


def render_fragment(source: str, content: str, *, max_chars: int = 4000) -> str:
    return LifeContextFragment(source=source, content=content, max_chars=max_chars).render()


def _safe_attr(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^a-zA-Z0-9_:.+@/-]+", "_", text)
    return text[:120] or "runtime"
