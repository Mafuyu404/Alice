"""Model-visible life context fragments.

These fragments are provenance and routing wrappers. They do not decide
importance or replace the LLM's judgment; they keep source, audience, role and
size boundaries explicit so different model calls can receive different views.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class LifeContextFragment:
    source: str
    content: str
    kind: str = "material"
    audience: str = "life_tick"
    role: str = "environment"
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    max_chars: int = 4000

    def render(self) -> str:
        source = _safe_attr(self.source or "runtime")
        kind = _safe_attr(self.kind or "material")
        audience = _safe_attr(self.audience or "life_tick")
        role = _safe_attr(self.role or "environment")
        created_at = _safe_attr(self.created_at)
        max_chars = max(200, int(self.max_chars or 4000))
        content = str(self.content or "").strip()
        if len(content) > max_chars:
            content = content[-max_chars:].strip()
        if not content:
            content = "(none)"
        return (
            f'<life_context kind="{kind}" audience="{audience}" role="{role}" '
            f'source="{source}" created_at="{created_at}" max_chars="{max_chars}">\n'
            f"{content}\n"
            "</life_context>"
        )


def render_fragment(
    source: str,
    content: str,
    *,
    max_chars: int = 4000,
    kind: str = "material",
    audience: str = "life_tick",
    role: str = "environment",
) -> str:
    return LifeContextFragment(
        source=source,
        content=content,
        max_chars=max_chars,
        kind=kind,
        audience=audience,
        role=role,
    ).render()


def _safe_attr(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^a-zA-Z0-9_:.+@/-]+", "_", text)
    return text[:120] or "runtime"
