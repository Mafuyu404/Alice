"""Public vision analysis workflows."""

from __future__ import annotations

import logging
import time

from kokoro.core import config as cfg
from kokoro.core import prompts
from kokoro.action.tools.observe_screen.vision_apps import format_apps, get_foreground_app, get_running_apps
from kokoro.action.tools.observe_screen.vision_backend import _vision_result
from kokoro.action.tools.observe_screen.vision_config import DEFAULT_DASHSCOPE_MODEL, KEY_BACKEND, KEY_MODEL
from kokoro.action.tools.observe_screen.vision_screenshot import screenshot_to_base64

logger = logging.getLogger("vision")


def analyze_image(
    image_uri: str,
    prompt: str,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    backend: str | None = None,
    timeout: int = 120,
    function: str = "vision",
) -> str:
    """Send a single image + prompt to the vision model and return the text response."""
    conf = cfg.load()
    if backend is None:
        backend = conf.get(KEY_BACKEND, "") or "dashscope"
    if model is None:
        model = conf.get(KEY_MODEL, "") or (
            DEFAULT_DASHSCOPE_MODEL if backend == "dashscope" else "qwen2.5vl:3b")
    return _vision_result([(image_uri, prompt)], model, backend, base_url, api_key, timeout, function=function)


def batch_analyze_images(
    items: list[tuple[str, str]],
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    backend: str | None = None,
    timeout: int = 120,
    function: str = "vision",
) -> str:
    """Send multiple (image_uri, prompt) pairs in a single vision API call.

    Each pair becomes a separate user message. The model processes all images
    in one request and returns a combined response.

    Useful for analyzing multiple screenshots, comparing screen states, or
    running different analyses in a single call.

    Parameters
    ----------
    items : list[tuple[str, str]]
        List of ``(image_data_uri, prompt_text)`` pairs.
    model : str | None
        Model name.  Defaults from config (``vision_model``) or per-backend default.
    base_url : str | None
        Ollama base URL (only used when backend is ``"ollama"``).
    api_key : str | None
        DashScope API key (only used when backend is ``"dashscope"``).
    backend : str | None
        ``"ollama"`` or ``"dashscope"``.  Falls back to config key ``vision_backend``.
    timeout : int
        HTTP request timeout in seconds.
    """
    if not items:
        return ""
    conf = cfg.load()
    if backend is None:
        backend = conf.get(KEY_BACKEND, "") or "dashscope"
    if model is None:
        model = conf.get(KEY_MODEL, "") or (
            DEFAULT_DASHSCOPE_MODEL if backend == "dashscope" else "qwen2.5vl:3b")
    return _vision_result(items, model, backend, base_url, api_key, timeout, function=function)


def describe(
    prompt: str = "",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    backend: str | None = None,
    timeout: int = 120,
    function: str = "vision_describe",
) -> str:
    """Capture full screen and run vision recognition.

    Parameters
    ----------
    prompt : str
        Text prompt sent alongside the image.
    model : str | None
        Model name.  Defaults from config (``vision_model``) or per-backend default.
    base_url : str | None
        Ollama base URL (only used when backend is ``"ollama"``).
        Falls back to ``llm_url`` from config.
    api_key : str | None
        DashScope API key (only used when backend is ``"dashscope"``).
        Falls back to config key ``vision_api_key``, then ``DASHSCOPE_API_KEY`` env.
    backend : str | None
        ``"ollama"`` or ``"dashscope"``.  Falls back to config key ``vision_backend``.
        Defaults to ``"dashscope"``.
    timeout : int
        HTTP request timeout in seconds.
    """
    if not prompt:
        prompt = prompts.get("vision.describe_default", "请详细描述这张图片中的内容")
    conf = cfg.load()
    if backend is None:
        backend = conf.get(KEY_BACKEND, "") or "dashscope"
    if model is None:
        model = conf.get(KEY_MODEL, "") or (
            DEFAULT_DASHSCOPE_MODEL if backend == "dashscope" else "qwen2.5vl:3b")

    t0 = time.time()
    image_uri = screenshot_to_base64()
    logger.info("screenshot captured in %.1fs", time.time() - t0)

    t1 = time.time()
    result = _vision_result([(image_uri, prompt)], model, backend, base_url, api_key, timeout, function=function)
    logger.info("vision response in %.1fs", time.time() - t1)
    return result


def detect_desktop(
    prompt: str = "",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    backend: str | None = None,
    timeout: int = 120,
    function: str = "vision_detect_desktop",
) -> str:
    """Capture screenshot, enumerate running windows, and describe the full desktop state.

    Combines the vision model's reading of the screenshot with live process
    information so you get both *what is visually on screen* and *what
    applications are running in the background*.

    Returns the model's text response.
    """
    # --- resolve backend / model ---
    conf = cfg.load()
    if backend is None:
        backend = conf.get(KEY_BACKEND, "") or "dashscope"
    if model is None:
        model = conf.get(KEY_MODEL, "") or (
            DEFAULT_DASHSCOPE_MODEL if backend == "dashscope" else "qwen2.5vl:3b")

    # --- gather system info ---
    t0 = time.time()
    image_uri = screenshot_to_base64()
    apps = get_running_apps()
    foreground = get_foreground_app()
    app_text = format_apps(apps, foreground)
    logger.info("gathered %d apps + screenshot in %.1fs", len(apps), time.time() - t0)

    # --- compose prompt ---
    suffix = prompts.format_prompt("vision.analyze_suffix", app_text=app_text)
    full_prompt = f"{prompt}{suffix}"

    t1 = time.time()
    result = _vision_result([(image_uri, full_prompt)], model, backend, base_url, api_key, timeout, function=function)
    logger.info("vision response in %.1fs", time.time() - t1)
    return result
