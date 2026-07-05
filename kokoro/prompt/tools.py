"""Tool prompt manifest loading.

Tool modules can optionally expose prompt resources next to their runtime code:

```
tool_name/
  manifest.toml
  prepare.md
  after.md
```

The loader only describes these resources. It does not decide when a tool should
be used and does not inject long tool prompts into the life loop by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib


@dataclass(frozen=True)
class ToolPromptSpec:
    id: str
    name: str
    actions: tuple[str, ...] = ()
    description: str = ""
    needs_prepare_llm: bool = False
    needs_after_llm: bool = False
    prepare_prompt: str = ""
    after_prompt: str = ""
    module_path: Path | None = None

    def catalog_line(self) -> str:
        suffixes: list[str] = []
        if self.needs_prepare_llm:
            suffixes.append("prepare LLM")
        if self.needs_after_llm:
            suffixes.append("after LLM")
        suffix = f" ({', '.join(suffixes)})" if suffixes else ""
        action_text = ", ".join(self.actions) if self.actions else self.name
        description = f": {self.description}" if self.description else ""
        return f"- {action_text}{description}{suffix}"

    def prompt(self, stage: str) -> str:
        if stage == "prepare":
            return self.prepare_prompt
        if stage == "after":
            return self.after_prompt
        raise ValueError(f"unknown tool prompt stage: {stage}")


def load_tool_prompt_spec(tool_dir: str | Path) -> ToolPromptSpec | None:
    path = Path(tool_dir)
    manifest_path = path / "manifest.toml"
    if not manifest_path.exists():
        return None
    with manifest_path.open("rb") as file:
        data = tomllib.load(file)
    tool_id = str(data.get("id") or path.name).strip()
    name = str(data.get("name") or tool_id).strip()
    if not tool_id or not name:
        raise ValueError(f"invalid tool prompt manifest: {manifest_path}")
    return ToolPromptSpec(
        id=tool_id,
        name=name,
        actions=tuple(str(item).strip() for item in data.get("actions", []) if str(item).strip()),
        description=str(data.get("description") or "").strip(),
        needs_prepare_llm=bool(data.get("needs_prepare_llm", False)),
        needs_after_llm=bool(data.get("needs_after_llm", False)),
        prepare_prompt=_read_optional(path, data.get("prepare_prompt")),
        after_prompt=_read_optional(path, data.get("after_prompt")),
        module_path=path,
    )


def discover_tool_prompt_specs(tools_root: str | Path) -> list[ToolPromptSpec]:
    root = Path(tools_root)
    if not root.exists():
        return []
    specs: list[ToolPromptSpec] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.name.startswith("__"):
            continue
        spec = load_tool_prompt_spec(child)
        if spec is not None:
            specs.append(spec)
    return specs


def index_tool_prompt_specs(specs: list[ToolPromptSpec]) -> dict[str, ToolPromptSpec]:
    index: dict[str, ToolPromptSpec] = {}
    for spec in specs:
        keys = spec.actions or (spec.name,)
        for key in keys:
            index[key] = spec
    return index


def render_tool_catalog(
    specs: list[ToolPromptSpec],
    enabled_actions: set[str] | list[str] | tuple[str, ...],
    *,
    include_stage_prompts: bool = False,
    stage_prompt_max_chars: int = 900,
) -> str:
    enabled = set(enabled_actions)
    lines: list[str] = []
    for spec in specs:
        actions = set(spec.actions or (spec.name,))
        if not actions.intersection(enabled):
            continue
        lines.append(_catalog_line_for_enabled_actions(spec, enabled))
        if include_stage_prompts:
            max_chars = max(120, int(stage_prompt_max_chars))
            if spec.prepare_prompt:
                lines.append(_stage_prompt_block("prepare", spec.prepare_prompt, max_chars=max_chars))
            if spec.after_prompt:
                lines.append(_stage_prompt_block("after", spec.after_prompt, max_chars=max_chars))
    return "\n".join(lines)


def _read_optional(base: Path, value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        return ""
    path = (base / name).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"tool prompt path escapes module directory: {name}") from exc
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _stage_prompt_block(stage: str, text: str, *, max_chars: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return f"  {stage}: {text}"


def _catalog_line_for_enabled_actions(spec: ToolPromptSpec, enabled: set[str]) -> str:
    suffixes: list[str] = []
    if spec.needs_prepare_llm:
        suffixes.append("prepare LLM")
    if spec.needs_after_llm:
        suffixes.append("after LLM")
    suffix = f" ({', '.join(suffixes)})" if suffixes else ""
    actions = tuple(action for action in (spec.actions or (spec.name,)) if action in enabled)
    action_text = ", ".join(actions) if actions else spec.name
    description = f": {spec.description}" if spec.description else ""
    return f"- {action_text}{description}{suffix}"
