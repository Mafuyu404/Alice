"""Screen capture + vision recognition + running-app enumeration (Windows)."""

from __future__ import annotations

import argparse
import base64
import io
import logging
import os
import sys
import time

from PIL import Image, ImageGrab

from kokoro import config as cfg
from kokoro import prompts

logger = logging.getLogger("vision")


def _max_pixels() -> int:
    """Return max pixel count from config (0 = no scaling)."""
    return cfg.vision_max_pixels()

# ---------------------------------------------------------------------------
# config keys
# ---------------------------------------------------------------------------
KEY_BACKEND = "vision_backend"
KEY_API_KEY = "vision_api_key"
KEY_MODEL = "vision_model"

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DEFAULT_DASHSCOPE_MODEL = "qwen-vl-plus"

# ---------------------------------------------------------------------------
# screenshot
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# running-application enumeration (Windows)
# ---------------------------------------------------------------------------

EXCLUDE_TITLES = {"", "Program Manager", "Settings", "Microsoft Text Input Application"}
EXCLUDE_PROCESSES = {"explorer.exe", "ApplicationFrameHost.exe", "shellexperiencehost.exe",
                     "SearchApp.exe", "TextInputHost.exe", "Widgets.exe", "StartMenuExperienceHost.exe"}
EXCLUDE_CLASSES = {"Shell_TrayWnd", "Windows.UI.Core.CoreWindow", "ApplicationFrameWindow",
                   "Windows.UI.Composition.DesktopWindowContentBridge",
                   "Progman", "WorkerW", "SysListView32", "#32770", "MultitaskingViewFrame",
                   "XamlExplorerHostIslandWindow", "SnipToolWindow"}


def _process_name(pid: int) -> str:
    """Get executable name from PID. Returns 'unknown' on failure."""
    try:
        import win32api
        import win32con
        import win32process

        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False, pid,
        )
        if not handle:
            return "unknown"
        try:
            path = win32process.GetModuleFileNameEx(handle, 0)
            return path.rsplit("\\", 1)[-1].lower() if path else "unknown"
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return "unknown"


def get_foreground_app() -> dict | None:
    """Return info about the currently focused window, or *None* if unavailable.

    Result keys: ``title``, ``process``, ``pid``, ``class_name``.
    """
    try:
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        return {"title": title, "process": _process_name(pid), "pid": pid, "class_name": cls}
    except Exception as exc:
        logger.debug("get_foreground_app failed: %s", exc)
        return None


def get_running_apps() -> list[dict]:
    """Enumerate all top-level visible windows with non-empty titles.

    Returns a list of dicts with ``title``, ``process``, ``pid``, ``class_name``.

    Shell background windows and known system chrome are filtered out.
    """
    import win32gui
    import win32process

    apps: list[dict] = []
    seen: set[str] = set()

    def _enum_cb(hwnd: int, _) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title or title in EXCLUDE_TITLES:
            return
        cls = win32gui.GetClassName(hwnd)
        if cls in EXCLUDE_CLASSES:
            return

        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        proc = _process_name(pid)
        if proc in EXCLUDE_PROCESSES:
            return

        key = f"{proc}|{title}|{pid}"
        if key in seen:
            return
        seen.add(key)

        apps.append({"title": title, "process": proc, "pid": pid, "class_name": cls})

    win32gui.EnumWindows(_enum_cb, None)
    return apps


def format_apps(apps: list[dict], foreground: dict | None) -> str:
    """Format enumerated apps and foreground window into a readable text block."""
    lines: list[str] = []

    if foreground and foreground.get("title"):
        lines.append(f"[前台焦点] {foreground['title']} ({foreground['process']}, PID={foreground['pid']})")
        lines.append("")

    if apps:
        lines.append("[正在运行的窗口]")
        # sort by process name for grouping
        sorted_apps = sorted(apps, key=lambda a: (a["process"], a["title"]))
        for a in sorted_apps:
            marker = " <- 前台" if (foreground and a["pid"] == foreground["pid"]) else ""
            lines.append(f"  - {a['title']} ({a['process']}, PID={a['pid']}){marker}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# backends
# ---------------------------------------------------------------------------


def _build_messages(items: list[tuple[str, str]]) -> list[dict]:
    """Build a list of user messages, one per (image_uri, prompt) pair."""
    messages = []
    for image_uri, prompt in items:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_uri}},
            ],
        })
    return messages


def _call_ollama(items: list[tuple[str, str]], model: str, base_url: str, timeout: int) -> str:
    import requests

    api_url = base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": _build_messages(items),
        "stream": False,
    }
    resp = requests.post(api_url, json=payload, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"Ollama API error {resp.status_code}: {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"]


def _call_dashscope(items: list[tuple[str, str]], model: str, api_key: str, timeout: int) -> str:
    import requests

    payload = {
        "model": model,
        "messages": _build_messages(items),
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    resp = requests.post(DASHSCOPE_URL, json=payload, headers=headers, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"DashScope API error {resp.status_code}: {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"]


def _safe_print(text: str) -> None:
    """Write text to stdout, handling encoding issues on Windows."""
    try:
        sys.stdout.write(text + "\n")
    except UnicodeEncodeError:
        # fallback: replace characters the console codepage can't handle
        safe = text.encode(sys.stdout.encoding or "gbk", errors="replace").decode(
            sys.stdout.encoding or "gbk")
        sys.stdout.write(safe + "\n")
    sys.stdout.flush()


def _vision_result(items: list[tuple[str, str]], model: str, backend: str,
                   base_url: str | None, api_key: str | None, timeout: int) -> str:
    """Route a batch of (image_uri, prompt) pairs to the correct backend.

    Each pair becomes a separate user message in a single API request.
    Returns the model's combined text response.
    """
    conf = cfg.load()

    if backend == "ollama":
        url = (base_url or cfg.llm_url()).rstrip("/")
        logger.info("ollama %s  %s  items=%d  timeout=%ss", model, url, len(items), timeout)
        return _call_ollama(items, model, url, timeout)

    key = api_key or conf.get(KEY_API_KEY) or os.environ.get("DASHSCOPE_API_KEY") or ""
    if not key:
        raise RuntimeError(
            "DashScope API key not set.  Provide --api-key, set config "
            f"'{KEY_API_KEY}', or export DASHSCOPE_API_KEY."
        )
    logger.info("dashscope %s  items=%d  timeout=%ss", model, len(items), timeout)
    return _call_dashscope(items, model, key, timeout)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def analyze_image(
    image_uri: str,
    prompt: str,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    backend: str | None = None,
    timeout: int = 120,
) -> str:
    """Send a single image + prompt to the vision model and return the text response."""
    conf = cfg.load()
    if backend is None:
        backend = conf.get(KEY_BACKEND, "") or "dashscope"
    if model is None:
        model = conf.get(KEY_MODEL, "") or (
            DEFAULT_DASHSCOPE_MODEL if backend == "dashscope" else "qwen2.5vl:3b")
    return _vision_result([(image_uri, prompt)], model, backend, base_url, api_key, timeout)


def batch_analyze_images(
    items: list[tuple[str, str]],
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    backend: str | None = None,
    timeout: int = 120,
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
    return _vision_result(items, model, backend, base_url, api_key, timeout)


def describe(
    prompt: str = "请详细描述这张图片中的内容",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    backend: str | None = None,
    timeout: int = 120,
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
    result = _vision_result([(image_uri, prompt)], model, backend, base_url, api_key, timeout)
    logger.info("vision response in %.1fs", time.time() - t1)
    return result


def detect_desktop(
    prompt: str = "",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    backend: str | None = None,
    timeout: int = 120,
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
    result = _vision_result([(image_uri, full_prompt)], model, backend, base_url, api_key, timeout)
    logger.info("vision response in %.1fs", time.time() - t1)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Screen capture + vision recognition")
    parser.add_argument("--prompt", "-p", default="请详细描述这张图片中的内容")
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--backend", "-b", default=None, choices=["ollama", "dashscope"])
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--timeout", "-t", type=int, default=120)
    parser.add_argument("--no-apps", action="store_true",
                        help="Screenshot only, skip running-app info")
    parser.add_argument("--apps", action="store_true",
                        help="List running windows and exit (no vision call)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        if args.apps:
            apps = get_running_apps()
            fg = get_foreground_app()
            _safe_print(format_apps(apps, fg))
            return

        fn = describe if args.no_apps else detect_desktop
        text = fn(
            prompt=args.prompt,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            backend=args.backend,
            timeout=args.timeout,
        )
        _safe_print(text)
    except Exception as exc:
        logger.error("vision error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
