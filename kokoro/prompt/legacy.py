"""Compatibility loader for existing ``prompts/`` assets.

This module is the migration bridge from loose ``kokoro.core.prompts`` access to
the structured prompt package. Existing callers can keep their old API while the
actual loading and template validation live under ``kokoro.prompt``.
"""

from __future__ import annotations

import os
import re
import string
import tomllib
from typing import Any

from kokoro.prompt.renderer import StrictRenderer
from kokoro.prompt.templates import load_template

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PROMPTS_DIR = os.path.join(_PROJECT_ROOT, "prompts")
_SKILLS_DIR = os.path.join(_PROMPTS_DIR, "skills")

_CACHE: dict[str, Any] | None = None
_SKILL_CACHE: dict[str, str] = {}
_FORMATTER = string.Formatter()
_STRICT_RENDERER = StrictRenderer()
_TEMPLATE_OVERRIDES: dict[str, str] = {
    "life_runtime.tick_system": "life/base.md",
    "life_runtime.tick_user": "life/inner_stream_tick.md",
    "life_runtime.context_compact_system": "life/context_compact_system.md",
    "life_runtime.context_compact_user": "life/context_compact_user.md",
    "life_runtime.patch_fallback_system": "life/patch_fallback_system.md",
    "life_runtime.patch_fallback_user": "life/patch_fallback_user.md",
    "life_runtime.json_repair_system": "life/json_repair_system.md",
    "life_runtime.json_repair_user": "life/json_repair_user.md",
    "cognition.evaluate_system": "memory/cognition_evaluate_system.md",
    "cognition.evaluate_user": "memory/cognition_evaluate_user.md",
    "cognition.autonomous_events_context": "memory/cognition_autonomous_events_context.md",
    "emotion.evaluate_system": "memory/emotion_evaluate_system.md",
    "emotion.evaluate_user": "memory/emotion_evaluate_user.md",
    "memory_events.memory_lookup": "memory/events_memory_lookup.md",
    "memory_events.extract_system": "memory/events_extract_system.md",
    "memory_events.extract_user": "memory/events_extract_user.md",
    "memory_events.summarize_system": "memory/events_summarize_system.md",
    "memory_events.summarize_user": "memory/events_summarize_user.md",
    "memory_importance.user_template": "memory/importance_user.md",
    "inner_memory_reflection.system": "memory/reflection_system.md",
    "inner_memory_reflection.user": "memory/reflection_user.md",
}
_TEMPLATE_GROUPS: dict[str, str] = {
    "autonomous_step": "life",
    "inner_stream": "life",
    "dialogue_orchestrator": "dialogue",
    "multi_dialogue_orchestrator": "dialogue",
    "overlap": "dialogue",
    "stt_refine": "dialogue",
    "stt_refine_inline": "dialogue",
    "conversation_summary": "dialogue",
    "scene": "dialogue",
    "chat_session": "dialogue",
    "qq": "dialogue",
    "character_system": "character",
    "vision": "vision",
    "screen_interest": "vision",
    "user_commands": "vision",
    "edge_cache": "vision",
    "qq_image": "vision",
    "tool_calling": "tools",
    "tool_handlers": "tools",
    "agent_guard": "tools",
    "bilibili_live": "tools",
    "claude_code_exec": "tools",
    "cli": "tools",
    "deepseek_api": "tools",
    "portrait_selection": "tools",
    "vts_body": "tools",
    "web_search_impulse": "tools",
}


def load() -> dict[str, Any]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    merged: dict[str, Any] = {}
    if os.path.isdir(_PROMPTS_DIR):
        for filename in sorted(os.listdir(_PROMPTS_DIR)):
            if not filename.endswith(".toml"):
                continue
            path = os.path.join(_PROMPTS_DIR, filename)
            try:
                with open(path, "rb") as file:
                    data = tomllib.load(file)
            except Exception:
                continue
            if isinstance(data, dict):
                _deep_merge(merged, data)
    _CACHE = merged
    return _CACHE


def reload() -> dict[str, Any]:
    global _CACHE
    _CACHE = None
    _SKILL_CACHE.clear()
    return load()


def get(path: str, default: Any = "") -> Any:
    override = _template_override(path)
    if override is not None:
        return _strict_to_legacy_template(override)
    current: Any = load()
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def format_prompt(path: str, **values: Any) -> str:
    override = _template_override(path)
    if override is not None:
        return render_strict_template(override, values)
    template = get(path, "")
    if not isinstance(template, str):
        return ""
    return render_legacy_template(template, values)


def render_legacy_template(template: str, values: dict[str, Any]) -> str:
    required = template_variables(template, strict=False)
    provided = set(values)
    missing = required - provided
    extra = provided - required
    if missing:
        from kokoro.prompt.renderer import TemplateRenderError

        raise TemplateRenderError(f"missing template variables: {', '.join(sorted(missing))}")
    if extra:
        from kokoro.prompt.renderer import TemplateRenderError

        raise TemplateRenderError(f"unused template variables: {', '.join(sorted(extra))}")
    return str(template or "").format(**values)


def render_strict_template(template: str, values: dict[str, Any]) -> str:
    return _STRICT_RENDERER.render(template, values)


def skill(name: str, default: str = "") -> str:
    safe_name = str(name or "").strip().replace("\\", "/").strip("/")
    if not safe_name or ".." in safe_name.split("/"):
        return default
    if safe_name in _SKILL_CACHE:
        return _SKILL_CACHE[safe_name]

    path = os.path.join(_SKILLS_DIR, f"{safe_name}.md")
    try:
        with open(path, "r", encoding="utf-8") as file:
            text = file.read().strip()
    except Exception:
        text = default
    _SKILL_CACHE[safe_name] = text
    return text


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def _brace_template_to_strict(template: str) -> str:
    result = str(template or "")
    for name in sorted(_legacy_variables(result), key=len, reverse=True):
        result = result.replace("{" + name + "}", "{{ " + name + " }}")
    return result


def template_variables(template: str, *, strict: bool = False) -> set[str]:
    if strict:
        return _STRICT_RENDERER.variables(template)
    return _legacy_variables(template)


def variables_for_path(path: str) -> set[str]:
    override = _template_override(path)
    if override is not None:
        return template_variables(override, strict=True)
    template = get(path, "")
    if not isinstance(template, str):
        return set()
    return template_variables(template, strict=False)


def has_template_override(path: str) -> bool:
    template_path = _template_override_path(path)
    if not template_path:
        return False
    from pathlib import Path

    root = Path(__file__).resolve().parent / "templates"
    target = (root / template_path).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return False
    return target.exists()


def _legacy_variables(template: str) -> set[str]:
    names: set[str] = set()
    for _, field_name, _, _ in _FORMATTER.parse(str(template or "")):
        if not field_name:
            continue
        name = str(field_name).split(".", 1)[0].split("[", 1)[0]
        if name:
            names.add(name)
    return names


def _template_override(path: str) -> str | None:
    template_path = _template_override_path(path)
    if not template_path:
        return None
    template = load_template(template_path)
    if template:
        return template
    return "" if has_template_override(path) else None


def _template_override_path(path: str) -> str | None:
    return _TEMPLATE_OVERRIDES.get(str(path)) or _conventional_template_path(str(path))


def _conventional_template_path(path: str) -> str | None:
    if "." not in path:
        return None
    module, key = path.split(".", 1)
    group = _TEMPLATE_GROUPS.get(module)
    if not group:
        return None
    safe_key = key.replace(".", "_")
    return f"{group}/{module}/{safe_key}.md"


def _strict_to_legacy_template(template: str) -> str:
    return re.sub(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}", r"{\1}", str(template or ""))
