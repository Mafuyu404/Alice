"""Screen capture helpers for observe_screen."""

from __future__ import annotations

import base64
import io
import logging

from PIL import Image, ImageGrab

from kokoro.action.tools.observe_screen.vision_config import _max_pixels

logger = logging.getLogger("vision")


def screenshot_to_base64(all_screens: bool = True, max_pixels: int | None = None) -> str:
    """Capture screen, downscale if needed, return base64-encoded PNG data URI."""
    img = ImageGrab.grab(all_screens=all_screens)
    w, h = img.size
    limit = _max_pixels() if max_pixels is None else max_pixels
    if limit > 0 and w * h > limit:
        ratio = (limit / (w * h)) ** 0.5
        new_w = int(w * ratio)
        new_h = int(h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        logger.info("downscaled screenshot %dx%d -> %dx%d", w, h, new_w, new_h)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def get_foreground_bounds() -> dict | None:
    """Get the bounding rect (left, top, right, bottom) of the foreground window."""
    try:
        import win32gui

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        rect = win32gui.GetWindowRect(hwnd)
        return {"left": rect[0], "top": rect[1], "right": rect[2], "bottom": rect[3]}
    except Exception:
        return None


def foreground_screenshot_to_base64(max_pixels: int | None = None) -> str | None:
    """Capture full screen, crop to the foreground window, return base64 PNG data URI.

    Returns *None* if the foreground window bounds cannot be determined or are empty.
    """
    bounds = get_foreground_bounds()
    if not bounds:
        return None
    l, t, r, b = bounds["left"], bounds["top"], bounds["right"], bounds["bottom"]
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return None

    img = ImageGrab.grab(all_screens=True)
    cropped = img.crop((l, t, r, b))
    limit = _max_pixels() if max_pixels is None else max_pixels
    if limit > 0 and w * h > limit:
        ratio = (max_pixels / (w * h)) ** 0.5
        new_w = int(w * ratio)
        new_h = int(h * ratio)
        cropped = cropped.resize((new_w, new_h), Image.LANCZOS)
        logger.info("downscaled foreground crop %dx%d -> %dx%d", w, h, new_w, new_h)
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"
