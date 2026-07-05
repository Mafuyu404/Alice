"""Default prompt registry composition."""

from __future__ import annotations

from . import life_runtime
from kokoro.prompt.registry import PromptRegistry


def build_default_registry() -> PromptRegistry:
    registry = PromptRegistry()
    life_runtime.register(registry)
    return registry
