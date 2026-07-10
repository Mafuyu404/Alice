"""Life runtime prompt fragments."""

from __future__ import annotations

from typing import Any

from kokoro.prompt import legacy as legacy_prompts
from kokoro.prompt.context import PromptContext
from kokoro.prompt.contracts import (
    LIFE_CONTEXT_COMPACT_SCENE,
    LIFE_JSON_REPAIR_SCENE,
    LIFE_PATCH_FALLBACK_SCENE,
    LIFE_TICK_SCENE,
    LIFE_TOOL_SELECT_SCENE,
)
from kokoro.prompt.fragment import PromptFragment
from kokoro.prompt.registry import PromptRegistry
from kokoro.prompt.templates import load_template


def register(registry: PromptRegistry) -> None:
    registry.register(
        LIFE_TICK_SCENE,
        PromptFragment(
            id="life_runtime.tick_system",
            role="system",
            scope="global",
            priority=10,
            template=_template_or_legacy("life/base.md", "life_runtime.tick_system"),
            values=lambda ctx: {"name": ctx.character_name or ctx.character_id or "AI"},
            marker=("<life_contract>", "</life_contract>"),
        ),
    )
    _register_pair(
        registry,
        scene=LIFE_TOOL_SELECT_SCENE,
        prefix="life_runtime.tool_select",
        user_values=lambda ctx: {
            "name": ctx.character_name or ctx.character_id or "AI",
            "action_intent": ctx.get("action_intent", "(none)") or "(none)",
            "inner_stream": ctx.get("inner_stream", "(empty)") or "(empty)",
            "time_context": ctx.get("time_context", "(none)") or "(none)",
            "context_digest": ctx.get("context_digest", "(none)") or "(none)",
            "tool_capabilities": ctx.get("tool_capabilities", "(none)") or "(none)",
        },
    )
    registry.register(
        LIFE_TICK_SCENE,
        PromptFragment(
            id="life_runtime.tick_user",
            role="user",
            scope="runtime",
            priority=100,
            template=_template_or_legacy("life/inner_stream_tick.md", "life_runtime.tick_user"),
            values=_life_tick_values,
        ),
    )
    _register_pair(
        registry,
        scene=LIFE_CONTEXT_COMPACT_SCENE,
        prefix="life_runtime.context_compact",
        user_values=lambda ctx: {
            "time_context": ctx.get("time_context", "(none)") or "(none)",
            "inner_stream": ctx.get("inner_stream", "(empty)") or "(empty)",
            "previous_digest": ctx.get("previous_digest", "(none)") or "(none)",
            "pending_threads": ctx.get("pending_threads", "(none)") or "(none)",
            "tool_results_digest": ctx.get("tool_results_digest", "(none)") or "(none)",
            "live_events": ctx.get("live_events", "(none)") or "(none)",
        },
    )
    _register_pair(
        registry,
        scene=LIFE_JSON_REPAIR_SCENE,
        prefix="life_runtime.json_repair",
        user_values=lambda ctx: {
            "parse_reason": ctx.get("parse_reason", "(unknown)") or "(unknown)",
            "raw_output": ctx.get("raw_output", ""),
        },
    )
    _register_pair(
        registry,
        scene=LIFE_PATCH_FALLBACK_SCENE,
        prefix="life_runtime.patch_fallback",
        user_values=lambda ctx: {
            "inner_stream": ctx.get("inner_stream", "(empty)") or "(empty)",
            "raw_patch": ctx.get("raw_patch", ""),
            "failure_reason": ctx.get("failure_reason", "(unknown)") or "(unknown)",
        },
    )


def _register_pair(registry: PromptRegistry, *, scene: str, prefix: str, user_values) -> None:
    registry.register(
        scene,
        PromptFragment(
            id=f"{prefix}_system",
            role="system",
            scope="global",
            priority=10,
            template=_template_or_legacy(
                _life_template_path(prefix, "system"),
                f"{prefix}_system",
            ),
        ),
    )
    registry.register(
        scene,
        PromptFragment(
            id=f"{prefix}_user",
            role="user",
            scope="runtime",
            priority=100,
            template=_template_or_legacy(
                _life_template_path(prefix, "user"),
                f"{prefix}_user",
            ),
            values=user_values,
        ),
    )


def _template_or_legacy(template_path: str, legacy_path: str) -> str:
    template = load_template(template_path)
    if template:
        return template
    return _legacy_format_template(legacy_path)


def _legacy_format_template(path: str) -> str:
    value = legacy_prompts.get(path, "")
    if not isinstance(value, str):
        return ""
    return legacy_prompts._brace_template_to_strict(value)


def _life_template_path(prefix: str, role: str) -> str:
    name = prefix.rsplit(".", 1)[-1]
    return f"life/{name}_{role}.md"


def _life_tick_values(ctx: PromptContext) -> dict[str, Any]:
    return {
        "name": ctx.character_name or ctx.character_id or "AI",
        "character_profile": ctx.get("character_profile", "(none)") or "(none)",
        "cognition_context": ctx.get("cognition_context", "(none)") or "(none)",
        "memory_context": ctx.get("memory_context", "(none)") or "(none)",
        "inner_stream": ctx.get("inner_stream", "(empty)") or "(empty)",
        "inner_stream_version": ctx.get("inner_stream_version", 0),
        "time_context": ctx.get("time_context", "(none)") or "(none)",
        "context_digest": ctx.get("context_digest", "(none)") or "(none)",
        "event_batch": ctx.get(
            "event_batch",
            "(no new external event; this can be time passing or continued thinking)",
        )
        or "(no new external event; this can be time passing or continued thinking)",
        "pending_threads": ctx.get("pending_threads", "(none)") or "(none)",
    }
