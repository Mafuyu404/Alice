"""Filesystem-backed prompt templates."""

from __future__ import annotations

from pathlib import Path

_TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"


def load_template(relative_path: str, default: str = "") -> str:
    safe = str(relative_path or "").strip().replace("\\", "/").strip("/")
    if not safe or ".." in safe.split("/"):
        return default
    path = (_TEMPLATE_ROOT / safe).resolve()
    try:
        path.relative_to(_TEMPLATE_ROOT.resolve())
    except ValueError:
        return default
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8").strip()
