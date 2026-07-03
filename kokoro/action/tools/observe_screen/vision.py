"""Screen capture + vision recognition + running-app enumeration (Windows)."""

from kokoro.action.tools.observe_screen.vision_apps import (
    EXCLUDE_CLASSES,
    EXCLUDE_PROCESSES,
    EXCLUDE_TITLES,
    _process_name,
    format_apps,
    get_foreground_app,
    get_running_apps,
)
from kokoro.action.tools.observe_screen.vision_backend import (
    _build_messages,
    _call_dashscope,
    _call_ollama,
    _vision_result,
)
from kokoro.action.tools.observe_screen.vision_cli import _safe_print, main
from kokoro.action.tools.observe_screen.vision_config import (
    DASHSCOPE_URL,
    DEFAULT_DASHSCOPE_MODEL,
    KEY_API_KEY,
    KEY_BACKEND,
    KEY_MODEL,
    _max_pixels,
)
from kokoro.action.tools.observe_screen.vision_public import (
    analyze_image,
    batch_analyze_images,
    describe,
    detect_desktop,
)
from kokoro.action.tools.observe_screen.vision_screenshot import (
    foreground_screenshot_to_base64,
    get_foreground_bounds,
    screenshot_to_base64,
)

__all__ = [
    "analyze_image",
    "batch_analyze_images",
    "describe",
    "detect_desktop",
    "format_apps",
    "foreground_screenshot_to_base64",
    "get_foreground_app",
    "get_foreground_bounds",
    "get_running_apps",
    "main",
    "screenshot_to_base64",
]


if __name__ == "__main__":
    main()
