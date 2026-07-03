"""QQ image download and local file preparation helpers."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import time
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from kokoro.action.tools.qq.media_models import QQImageRef

_ROOT = Path(__file__).resolve().parents[4]
_CACHE_DIR = _ROOT / "data" / "qq_images"


def download_image(ref: QQImageRef, *, timeout: float = 15.0, max_bytes: int = 8_000_000) -> str:
    url = ref.url or (ref.file if ref.file.startswith(("http://", "https://")) else "")
    if not url:
        raise ValueError("QQ image has no url")
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
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
