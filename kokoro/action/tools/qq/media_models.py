"""QQ media data models."""

from __future__ import annotations

from dataclasses import dataclass


class QQImageRef:
    raw: str
    url: str = ""
    file: str = ""
    summary: str = ""
    sub_type: str = ""


class QQImageUnderstanding:
    image: QQImageRef
    local_path: str
    description: str
    kind: str = "unknown"
    text: str = ""
    tone: str = ""
    context_meaning: str = ""

    def prompt_text(self) -> str:
        parts = [
            f"图片类型：{self.kind}",
            f"图片描述：{self.description}",
        ]
        if self.text:
            parts.append(f"图中文字：{self.text}")
        if self.tone:
            parts.append(f"语气/情绪：{self.tone}")
        if self.context_meaning:
            parts.append(f"结合上下文：{self.context_meaning}")
        return "\n".join(parts)
