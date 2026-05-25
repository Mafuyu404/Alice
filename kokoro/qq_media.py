"""QQ image download, vision understanding, and sticker save decisions."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, UnidentifiedImageError

from kokoro import prompts
from kokoro import vision
from kokoro.sticker_library import StickerLibrary


_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _ROOT / "data" / "qq_images"


@dataclass(frozen=True)
class QQImageRef:
    raw: str
    url: str = ""
    file: str = ""
    summary: str = ""
    sub_type: str = ""


@dataclass(frozen=True)
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


def download_image(ref: QQImageRef, *, timeout: float = 15.0, max_bytes: int = 8_000_000) -> str:
    url = ref.url or (ref.file if ref.file.startswith(("http://", "https://")) else "")
    if not url:
        raise ValueError("QQ image has no url")
    req = urllib.request.Request(url, headers={"User-Agent": "Alice/QQImageProcessor"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_type = resp.headers.get("content-type", "")
        data = resp.read(max_bytes + 1)
        status = getattr(resp, "status", "")
    if content_type and "image" not in content_type.lower():
        raise ValueError(f"QQ image response is not image content: status={status} content_type={content_type}")
    if len(data) > max_bytes:
        raise ValueError("QQ image is too large")
    suffix = _suffix_from(ref.file, content_type)
    path = _CACHE_DIR / f"{int(time.time())}_{uuid.uuid4().hex[:8]}{suffix}"
    path.write_bytes(data)
    return str(path)


def prepare_image_for_vision(local_path: str) -> str:
    path = Path(local_path)
    try:
        with Image.open(path) as img:
            frame_count = int(getattr(img, "n_frames", 1) or 1)
            image_format = str(img.format or "").upper()
            needs_png = image_format not in {"JPEG", "JPG", "PNG"} or frame_count > 1
            if not needs_png:
                return str(path)
            img.seek(0)
            frame = img.convert("RGBA")
            prepared = path.with_suffix(path.suffix + ".vision.png")
            frame.save(prepared, format="PNG")
            return str(prepared)
    except UnidentifiedImageError as exc:
        raise ValueError(f"downloaded QQ image is not a readable image: {path}") from exc


def image_fingerprint(local_path: str) -> str:
    try:
        with Image.open(local_path) as img:
            img.seek(0)
            small = img.convert("L").resize((16, 16), Image.Resampling.LANCZOS)
            pixels = list(small.getdata())
            avg = sum(pixels) / max(1, len(pixels))
            bits = "".join("1" if pixel >= avg else "0" for pixel in pixels)
            return f"p16:{int(bits, 2):064x}"
    except Exception:
        data = Path(local_path).read_bytes()
        return "sha1:" + hashlib.sha1(data).hexdigest()


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
    raw = vision.analyze_image(
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
    raw = vision.analyze_image(
        _file_to_data_uri(understood.local_path),
        prompt,
        timeout=45,
        function="qq_sticker_save_decision",
    )
    return _extract_json(raw) or {"save": False}


def sticker_candidates_text(limit: int = 18) -> str:
    return StickerLibrary().candidates_text(limit=limit)


def sticker_candidates_for_context(context: str, limit: int = 30) -> str:
    return StickerLibrary().search_candidates(context, limit=limit)


def resolve_sticker_path(sticker_id: str) -> str:
    return StickerLibrary().resolve_path(sticker_id)


def resolve_sticker(sticker_id: str) -> dict | None:
    return StickerLibrary().resolve_item(sticker_id)


def fallback_sticker(query: str = "", *, min_score: float = 0.45) -> dict | None:
    return StickerLibrary().fallback_item(query, min_score=min_score)


def retire_sticker(sticker_id: str, *, reason: str = "", actor: str = "") -> dict | None:
    return StickerLibrary().retire_item(sticker_id, reason=reason, actor=actor)


def _parse_cq_attrs(text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for part in str(text or "").split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        attrs[key.strip()] = html.unescape(urllib.parse.unquote(value.strip()))
    return attrs


def _debug_log(event: str, **fields) -> None:
    parts = [f"[qq_image] {event}"]
    for key, value in fields.items():
        text = str(value)
        parts.append(f"{key}={text!r}")
    try:
        print(" ".join(parts), flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe = " ".join(parts).encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe, flush=True)


def _clip(text: str, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _fill_prompt(template: str, values: dict[str, str]) -> str:
    text = str(template or "")
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def _suffix_from(file: str, content_type: str) -> str:
    suffix = Path(file or "").suffix.lower()
    if suffix:
        return suffix[:12]
    guessed = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip())
    return guessed or ".png"


def _file_to_data_uri(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/png"
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _extract_json(text: str) -> dict | None:
    raw = str(text or "").strip().lstrip("\ufeff")
    if not raw:
        return None
    if "</think>" in raw:
        raw = raw.split("</think>")[-1].strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    for match in re.finditer(r"```(?:json|JSON)?\s*\n?(.*?)```", raw, re.S):
        block = match.group(1).strip()
        try:
            value = json.loads(block)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            nested = _parse_json_object_slice(block)
            if nested:
                return nested
            salvaged = _salvage_json_fields(block)
            if salvaged:
                return salvaged
    return _parse_json_object_slice(raw) or _salvage_json_fields(raw)


def _parse_json_object_slice(text: str) -> dict | None:
    start = str(text or "").find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return value if isinstance(value, dict) else None
    return None


def _salvage_json_fields(text: str) -> dict | None:
    raw = str(text or "")
    keys = (
        "kind",
        "description",
        "text",
        "tone",
        "context_meaning",
        "save",
        "reason",
        "desc",
        "intensity",
        "avoid",
        "usage_notes",
    )
    data: dict = {}
    for key in keys:
        value = _extract_json_like_field(raw, key)
        if value != "":
            if key == "save":
                data[key] = value.lower() in {"true", "1", "yes", "是", "保存", "收藏"}
            else:
                data[key] = value
    for key in ("tags", "emotions", "scenes", "style"):
        values = _extract_json_like_array(raw, key)
        if values:
            data[key] = values
    if data:
        return data
    return None


def _extract_json_like_field(text: str, key: str) -> str:
    quoted = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.S)
    if quoted:
        value = quoted.group(1)
        try:
            return json.loads(f'"{value}"')
        except json.JSONDecodeError:
            return value.replace('\\"', '"').replace("\\n", "\n").strip()
    bare = re.search(rf'"{re.escape(key)}"\s*:\s*([^,\n\r}}]+)', text, re.S)
    return bare.group(1).strip().strip('"') if bare else ""


def _extract_json_like_array(text: str, key: str) -> list[str]:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\[(.*?)\]', text, re.S)
    if not match:
        return []
    raw_items = match.group(1)
    values = re.findall(r'"((?:\\.|[^"\\])*)"', raw_items)
    if not values:
        values = [part.strip().strip('"') for part in raw_items.split(",")]
    return [value.strip() for value in values if value.strip()][:8]


def _string_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()][:8]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,，、/]", value) if part.strip()][:8]
    return []


def _looks_generic_sticker_save(decision: dict, understood: QQImageUnderstanding) -> bool:
    desc = str(decision.get("desc") or understood.description or "")
    reason = str(decision.get("reason") or "")
    usage = str(decision.get("usage_notes") or decision.get("use") or "")
    combined = f"{desc}\n{reason}\n{usage}"
    generic_hits = sum(
        phrase in combined
        for phrase in (
            "多种日常聊天场景",
            "高适用性",
            "缓解尴尬",
            "轻松幽默",
            "技术讨论",
            "有趣或略显复杂的问题",
        )
    )
    has_specific_text = bool(str(understood.text or "").strip()) and "无明显" not in str(understood.text)
    emotions = _string_list(decision.get("emotions"))
    scenes = _string_list(decision.get("scenes"))
    if generic_hits >= 3 and not has_specific_text:
        return True
    if len(set(emotions + scenes)) <= 2 and not has_specific_text:
        return True
    return False


def _looks_like_non_sticker_information_image(text: str) -> bool:
    markers = (
        "item tags",
        "curios:curio",
        "minecraft",
        "物品栏",
        "背包",
        "饰品",
        "栏位",
        "属性",
        "半径增加",
        "tooltip",
        "物品描述",
        "配方",
        "百科",
        "wiki",
        "设置页",
        "控制台",
        "报错",
        "stack trace",
    )
    return any(marker in text for marker in markers)


def _looks_like_low_information_sticker(text: str, image_text: str) -> bool:
    has_visible_text = bool(str(image_text or "").strip()) and "无明显" not in str(image_text)
    if has_visible_text:
        return False
    specific_markers = (
        "哭",
        "怒",
        "震惊",
        "疑惑",
        "崩溃",
        "害羞",
        "得意",
        "嫌弃",
        "猫",
        "狗",
        "角色",
        "人物",
        "梗",
        "reaction",
        "meme",
    )
    generic_markers = ("可爱", "微笑", "卡通", "简单", "普通", "轻松", "日常", "无明显")
    return any(marker in text for marker in generic_markers) and not any(marker in text for marker in specific_markers)
