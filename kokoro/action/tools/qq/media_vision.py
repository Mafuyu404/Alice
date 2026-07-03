"""QQ image vision understanding and sticker-save LLM decisions."""

from __future__ import annotations

from kokoro.core import prompts
from kokoro.action.tools import observe_screen
from kokoro.action.tools.qq.media_files import _file_to_data_uri
from kokoro.action.tools.qq.media_models import QQImageRef, QQImageUnderstanding
from kokoro.action.tools.qq.media_utils import _extract_json, _fill_prompt


def understand_image(
    ref: QQImageRef,
    local_path: str,
    *,
    sender: str,
    context_lines: list[str],
    timeout: int = 45,
    model: str | None = None,
    backend: str | None = None,
) -> QQImageUnderstanding:
    image_uri = _file_to_data_uri(local_path)
    prompt = prompts.get("qq_image.understand", "") or prompts.get("qq_image.understand_default", "")
    prompt = _fill_prompt(
        prompt,
        {
            "sender": sender or "未知发送者",
            "summary": ref.summary or "无",
            "context": "\n".join(context_lines[-20:]) or "无",
        },
    )
    raw = observe_screen.analyze_image(
        image_uri,
        prompt,
        timeout=timeout,
        model=model,
        backend=backend,
        function="qq_image_understand",
    )
    data = _extract_json(raw)
    if not data:
        data = {"description": raw.strip()[:800], "kind": "unknown"}
    return QQImageUnderstanding(
        image=ref,
        local_path=local_path,
        description=str(data.get("description") or "").strip()[:1000],
        kind=str(data.get("kind") or "unknown").strip(),
        text=str(data.get("text") or "").strip()[:500],
        tone=str(data.get("tone") or "").strip()[:300],
        context_meaning=str(data.get("context_meaning") or "").strip()[:800],
    )


def decide_save_sticker(
    understood: QQImageUnderstanding,
    *,
    inner_stream: str,
    candidates: str,
) -> dict:
    prompt = prompts.get("qq_image.save_sticker", "") or _save_prompt()
    prompt = _fill_prompt(
        prompt,
        {
            "image": understood.prompt_text(),
            "inner_stream": inner_stream or "无",
            "candidates": candidates or "无",
        },
    )
    raw = observe_screen.analyze_image(
        _file_to_data_uri(understood.local_path),
        prompt,
        timeout=45,
        function="qq_sticker_save_decision",
    )
    return _extract_json(raw) or {"save": False}
