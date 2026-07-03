"""CQ image parsing helpers."""

from __future__ import annotations

import html
import re
import urllib.parse

from kokoro.action.tools.qq.media_models import QQImageRef


def extract_images(content: str) -> list[QQImageRef]:
    refs: list[QQImageRef] = []
    for match in re.finditer(r"\[CQ:image,([^\]]+)\]", str(content or "")):
        raw = match.group(0)
        attrs = _parse_cq_attrs(match.group(1))
        refs.append(
            QQImageRef(
                raw=raw,
                url=attrs.get("url", ""),
                file=attrs.get("file", ""),
                summary=attrs.get("summary", ""),
                sub_type=attrs.get("sub_type", ""),
            )
        )
    return refs


def _parse_cq_attrs(text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for part in str(text or "").split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        attrs[key.strip()] = html.unescape(urllib.parse.unquote(value.strip()))
    return attrs
