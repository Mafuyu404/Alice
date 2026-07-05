"""Prompt fragments and marker handling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Any

from kokoro.prompt.context import PromptContext
from kokoro.prompt.renderer import StrictRenderer
from kokoro.prompt.templates import load_template

PromptRole = Literal["system", "developer", "user"]
PromptScope = Literal["global", "character", "runtime", "tool", "debug"]


@dataclass(frozen=True)
class RenderedFragment:
    id: str
    role: PromptRole
    scope: PromptScope
    priority: int
    content: str
    char_count: int
    skipped_by_diff: bool = False
    truncated: bool = False


@dataclass
class PromptFragment:
    id: str
    role: PromptRole
    scope: PromptScope
    priority: int = 100
    template: str | None = None
    template_path: str | None = None
    marker: tuple[str, str] | None = None
    budget: int | None = None
    values: Callable[[PromptContext], dict[str, Any]] | None = None
    condition: Callable[[PromptContext], bool] | None = None
    snapshot: Callable[[PromptContext], dict[str, Any]] | None = None
    render_func: Callable[[PromptContext], str] | None = None

    def should_render(self, ctx: PromptContext) -> bool:
        return True if self.condition is None else bool(self.condition(ctx))

    def render(self, ctx: PromptContext, renderer: StrictRenderer) -> RenderedFragment:
        if self.render_func is not None:
            content = self.render_func(ctx)
        else:
            template = self.template
            if template is None and self.template_path:
                template = load_template(self.template_path)
            template = template or ""
            values = self.values(ctx) if self.values is not None else {}
            content = renderer.render(template, values)
        content = self._wrap_marker(str(content))
        truncated = False
        if self.budget is not None and self.budget >= 0 and len(content) > self.budget:
            content = content[: self.budget].rstrip()
            truncated = True
        return RenderedFragment(
            id=self.id,
            role=self.role,
            scope=self.scope,
            priority=self.priority,
            content=content,
            char_count=len(content),
            truncated=truncated,
        )

    def _wrap_marker(self, content: str) -> str:
        if not self.marker:
            return content
        start, end = self.marker
        return f"{start}\n{content.strip()}\n{end}".strip()
