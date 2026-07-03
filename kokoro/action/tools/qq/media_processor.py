"""QQ image processor orchestration."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from kokoro.action.tools.qq.media_files import _CACHE_DIR, download_image, image_fingerprint, prepare_image_for_vision
from kokoro.action.tools.qq.media_models import QQImageRef, QQImageUnderstanding
from kokoro.action.tools.qq.media_parsing import extract_images
from kokoro.action.tools.qq.media_stickers import (
    _looks_generic_sticker_save,
    _looks_like_low_information_sticker,
    _looks_like_non_sticker_information_image,
)
from kokoro.action.tools.qq.media_utils import _clip, _debug_log, _string_list
from kokoro.action.tools.qq.media_vision import decide_save_sticker, understand_image
from kokoro.action.tools.qq.sticker_library import StickerLibrary


class QQImageProcessor:
    def __init__(
        self,
        *,
        session,
        on_understood: Callable[[str, dict], None] | None = None,
        section: dict | None = None,
    ) -> None:
        section = section or {}
        self.session = session
        self.on_understood = on_understood
        self.enabled = bool(section.get("enabled", True))
        self.save_enabled = bool(section.get("auto_save_stickers", True))
        self.timeout = float(section.get("download_timeout", 15.0) or 15.0)
        self.max_bytes = int(section.get("max_image_bytes", 8_000_000) or 8_000_000)
        self.vision_timeout = int(section.get("vision_timeout", 45) or 45)
        self.model = str(section.get("model") or "").strip() or None
        self.backend = str(section.get("backend") or "").strip() or None
        self.save_screenshots = bool(section.get("save_screenshots", False))
        self.save_photos = bool(section.get("save_photos", False))
        self.library = StickerLibrary(section.get("sticker_dir") or None)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def consider_message(self, message, *, recent_lines: list[str] | None = None) -> None:
        if not self.enabled:
            return
        refs = extract_images(getattr(message, "content", ""))
        if not refs:
            return
        sender = getattr(message, "nickname", "") or getattr(message, "user_id", "")
        for ref in refs:
            _debug_log(
                "detected",
                conversation_id=getattr(message, "conversation_id", ""),
                sender=sender,
                file=ref.file,
                summary=ref.summary,
                url=_clip(ref.url, 180),
            )
        thread = threading.Thread(
            target=self._run,
            kwargs={"message": message, "refs": refs, "recent_lines": list(recent_lines or [])},
            daemon=True,
        )
        thread.start()

    def _run(self, *, message, refs: list[QQImageRef], recent_lines: list[str]) -> None:
        for ref in refs:
            try:
                _debug_log("download_start", file=ref.file, url=_clip(ref.url, 180))
                local_path = download_image(ref, timeout=self.timeout, max_bytes=self.max_bytes)
                _debug_log(
                    "download_ok",
                    path=local_path,
                    bytes=Path(local_path).stat().st_size if Path(local_path).exists() else 0,
                )
                vision_path = prepare_image_for_vision(local_path)
                if vision_path != local_path:
                    _debug_log(
                        "vision_prepare",
                        source=local_path,
                        path=vision_path,
                        bytes=Path(vision_path).stat().st_size if Path(vision_path).exists() else 0,
                    )
                _debug_log(
                    "vision_start",
                    path=vision_path,
                    backend=self.backend or "default",
                    model=self.model or "default",
                )
                understood = understand_image(
                    ref,
                    vision_path,
                    sender=getattr(message, "nickname", "") or getattr(message, "user_id", ""),
                    context_lines=recent_lines,
                    timeout=self.vision_timeout,
                    model=self.model,
                    backend=self.backend,
                )
                _debug_log(
                    "vision_ok",
                    kind=understood.kind,
                    desc=_clip(understood.description, 160),
                    text=_clip(understood.text, 120),
                )
                metadata = {
                    "conversation_id": getattr(message, "conversation_id", ""),
                    "message_type": getattr(message, "message_type", ""),
                    "group_id": getattr(message, "group_id", ""),
                    "sender": getattr(message, "nickname", "") or getattr(message, "user_id", ""),
                    "local_path": local_path,
                    "vision_path": vision_path,
                    "kind": understood.kind,
                }
                if self.on_understood:
                    self.on_understood(understood.prompt_text(), metadata)
                if self.save_enabled:
                    self._maybe_save(understood, metadata)
            except Exception as exc:
                _debug_log(
                    "error",
                    stage="process",
                    file=ref.file,
                    error=f"{type(exc).__name__}: {exc}",
                )
                if self.on_understood:
                    self.on_understood(
                        f"QQ 图片识别失败：{type(exc).__name__}: {exc}",
                        {
                            "conversation_id": getattr(message, "conversation_id", ""),
                            "message_type": getattr(message, "message_type", ""),
                            "file": ref.file,
                            "summary": ref.summary,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )

    def _maybe_save(self, understood: QQImageUnderstanding, metadata: dict) -> None:
        _debug_log("save_decision_start", path=understood.local_path, kind=understood.kind)
        if not self._is_collectible_sticker(understood):
            _debug_log("save_skip", reason="not_collectible_kind", kind=understood.kind)
            return
        fingerprint = image_fingerprint(understood.local_path)
        existing = self.library.find_similar(
            desc=understood.description,
            tags=[understood.kind, understood.tone],
            fingerprint=fingerprint,
        )
        if existing:
            _debug_log("save_skip", reason="duplicate", sticker_id=existing.get("id", ""))
            return
        decision = decide_save_sticker(
            understood,
            inner_stream=getattr(getattr(self.session, "inner_stream", None), "text", ""),
            candidates=self.library.candidates_text(limit=12),
        )
        _debug_log(
            "save_decision",
            save=bool(decision.get("save")),
            reason=_clip(str(decision.get("reason") or ""), 160),
            tags=",".join(str(t) for t in decision.get("tags", []) if str(t).strip())[:160],
        )
        if not decision.get("save"):
            return
        if _looks_generic_sticker_save(decision, understood):
            _debug_log("save_skip", reason="generic_save_decision")
            return
        tags = [str(t) for t in decision.get("tags", []) if str(t).strip()]
        existing = self.library.find_similar(
            desc=str(decision.get("desc") or understood.description),
            tags=tags,
            fingerprint=fingerprint,
        )
        if existing:
            _debug_log("save_skip", reason="duplicate_after_decision", sticker_id=existing.get("id", ""))
            return
        item = self.library.add_from_file(
            understood.local_path,
            desc=str(decision.get("desc") or understood.description),
            tags=tags,
            why_saved=str(decision.get("reason") or ""),
            source="qq",
            source_group=str(metadata.get("group_id") or ""),
            source_sender=str(metadata.get("sender") or ""),
            kind=understood.kind,
            fingerprint=fingerprint,
        )
        self.library.update_item(
            item["id"],
            {
                "text": understood.text,
                "emotions": _string_list(decision.get("emotions")),
                "scenes": _string_list(decision.get("scenes")),
                "style": _string_list(decision.get("style")),
                "intensity": str(decision.get("intensity") or "").strip(),
                "avoid": str(decision.get("avoid") or "").strip(),
                "usage_notes": str(decision.get("usage_notes") or decision.get("use") or "").strip(),
            },
        )
        _debug_log("saved", sticker_id=item["id"], path=item.get("path", ""))
        record = getattr(self.session, "record_self_action", None)
        if callable(record):
            record(
                f"我收藏了一张表情包：{item['desc']}。以后适合这样用：{item.get('why_saved') or '轻量接梗或表达情绪'}",
                source="sticker_library",
                action="save_sticker",
                metadata={"sticker_id": item["id"], "tags": item.get("tags", [])},
            )

    def _is_collectible_sticker(self, understood: QQImageUnderstanding) -> bool:
        kind = str(understood.kind or "").strip().lower()
        if kind in {"screenshot", "unknown"}:
            return False
        if kind == "photo" and not self.save_photos:
            return False
        if kind == "screenshot" and not self.save_screenshots:
            return False
        text = f"{understood.description}\n{understood.text}\n{understood.tone}\n{understood.context_meaning}".lower()
        if _looks_like_non_sticker_information_image(text):
            return False
        if any(word in text for word in ("截图", "搜索记录", "命令行", "网页", "配置", "表格", "链接", "代码")):
            return kind in {"meme", "sticker"} and any(word in text for word in ("表情", "meme", "梗图"))
        if kind in {"meme", "sticker"} and _looks_like_low_information_sticker(text, understood.text):
            return False
        return kind in {"meme", "sticker"} or (kind == "photo" and self.save_photos)
