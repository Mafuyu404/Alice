"""Structured prompt assembly for Kokoro."""

from kokoro.prompt.context import PromptContext
from kokoro.prompt.fragment import PromptFragment, RenderedFragment
from kokoro.prompt.manager import PromptManager
from kokoro.prompt.renderer import StrictRenderer, TemplateRenderError
from kokoro.prompt.templates import load_template
from kokoro.prompt.tools import (
    ToolPromptSpec,
    discover_tool_prompt_specs,
    index_tool_prompt_specs,
    load_tool_prompt_spec,
    render_tool_catalog,
)

__all__ = [
    "PromptContext",
    "PromptFragment",
    "PromptManager",
    "RenderedFragment",
    "StrictRenderer",
    "TemplateRenderError",
    "ToolPromptSpec",
    "discover_tool_prompt_specs",
    "index_tool_prompt_specs",
    "load_tool_prompt_spec",
    "load_template",
    "render_tool_catalog",
]
