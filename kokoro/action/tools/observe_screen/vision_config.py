"""Vision backend configuration constants."""

from __future__ import annotations

from kokoro.core import config as cfg

KEY_BACKEND = "vision_backend"
KEY_API_KEY = "vision_api_key"
KEY_MODEL = "vision_model"

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DEFAULT_DASHSCOPE_MODEL = "qwen-vl-plus"


def _max_pixels() -> int:
    """Return max pixel count from config (0 = no scaling)."""
    return cfg.vision_max_pixels()
