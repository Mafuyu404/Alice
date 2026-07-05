"""Strict prompt template rendering."""

from __future__ import annotations

import re
from typing import Any


class TemplateRenderError(ValueError):
    """Raised when a prompt template cannot be rendered safely."""


_PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


class StrictRenderer:
    """Render ``{{ name }}`` templates with exact variable validation."""

    def variables(self, template: str) -> set[str]:
        text = str(template or "")
        stripped = _PLACEHOLDER_RE.sub("", text)
        if "{{" in stripped:
            raise TemplateRenderError("unclosed or invalid template placeholder")
        return set(_PLACEHOLDER_RE.findall(text))

    def render(self, template: str, values: dict[str, Any]) -> str:
        provided = set(values)
        required = self.variables(template)
        missing = required - provided
        extra = provided - required
        if missing:
            raise TemplateRenderError(f"missing template variables: {', '.join(sorted(missing))}")
        if extra:
            raise TemplateRenderError(f"unused template variables: {', '.join(sorted(extra))}")

        def replace(match: re.Match[str]) -> str:
            return str(values[match.group(1)])

        return _PLACEHOLDER_RE.sub(replace, str(template or ""))
