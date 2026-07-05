"""Prompt fragment registry."""

from __future__ import annotations

from collections import defaultdict

from kokoro.prompt.context import PromptContext
from kokoro.prompt.fragment import PromptFragment


class PromptRegistry:
    def __init__(self) -> None:
        self._by_scene: dict[str, list[PromptFragment]] = defaultdict(list)

    def register(self, scene: str, fragment: PromptFragment) -> None:
        self._by_scene[str(scene)].append(fragment)

    def select(self, ctx: PromptContext) -> list[PromptFragment]:
        fragments = list(self._by_scene.get(ctx.scene, ()))
        return sorted(fragments, key=lambda item: (item.role, item.priority, item.id))
