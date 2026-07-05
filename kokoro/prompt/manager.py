"""Prompt manager and compatibility fragments."""

from __future__ import annotations

from kokoro.prompt.context import PromptContext
from kokoro.prompt.diagnostics import PromptTrace
from kokoro.prompt.fragment import RenderedFragment
from kokoro.prompt.fragments import build_default_registry
from kokoro.prompt.registry import PromptRegistry
from kokoro.prompt.renderer import StrictRenderer
from kokoro.prompt.state import PromptState


class PromptManager:
    """Assemble model messages from registered prompt fragments."""

    def __init__(
        self,
        *,
        registry: PromptRegistry | None = None,
        renderer: StrictRenderer | None = None,
        state: PromptState | None = None,
    ) -> None:
        self.registry = registry or default_registry()
        self.renderer = renderer or StrictRenderer()
        self.state = state or PromptState()
        self.last_trace: PromptTrace | None = None

    def render(self, scene: str, ctx: PromptContext) -> list[dict[str, str]]:
        if ctx.scene != scene:
            ctx = PromptContext(
                scene=scene,
                character_id=ctx.character_id,
                character_name=ctx.character_name,
                values=dict(ctx.values),
                debug_mode=ctx.debug_mode,
                trace_dir=ctx.trace_dir,
            )
        trace = PromptTrace(
            scene=scene,
            context={
                "character_id": ctx.character_id,
                "character_name": ctx.character_name,
                "debug_mode": ctx.debug_mode,
                "value_keys": sorted(ctx.values),
            },
            snapshots_before=dict(self.state.snapshots),
        )
        rendered: list[RenderedFragment] = []
        for fragment in self.registry.select(ctx):
            if not fragment.should_render(ctx):
                continue
            snapshot = fragment.snapshot(ctx) if fragment.snapshot is not None else None
            if not self.state.changed(fragment.id, snapshot):
                skipped = RenderedFragment(
                    id=fragment.id,
                    role=fragment.role,
                    scope=fragment.scope,
                    priority=fragment.priority,
                    content="",
                    char_count=0,
                    skipped_by_diff=True,
                )
                trace.add_fragment(skipped)
                continue
            item = fragment.render(ctx, self.renderer)
            rendered.append(item)
            trace.add_fragment(item)

        messages = _combine_messages(rendered)
        trace.messages = messages
        trace.snapshots_after = dict(self.state.snapshots)
        self.last_trace = trace
        if ctx.trace_dir:
            trace.write(ctx.trace_dir)
        return messages


def default_registry() -> PromptRegistry:
    return build_default_registry()


def _combine_messages(fragments: list[RenderedFragment]) -> list[dict[str, str]]:
    order = {"system": 0, "developer": 1, "user": 2}
    by_role: dict[str, list[RenderedFragment]] = {}
    for fragment in sorted(fragments, key=lambda item: (order.get(item.role, 99), item.priority, item.id)):
        by_role.setdefault(fragment.role, []).append(fragment)
    messages: list[dict[str, str]] = []
    for role in ("system", "developer", "user"):
        content = "\n\n".join(item.content for item in by_role.get(role, []) if item.content).strip()
        if content:
            messages.append({"role": role, "content": content})
    return messages
