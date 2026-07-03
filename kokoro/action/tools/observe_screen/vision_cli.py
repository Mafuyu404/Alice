"""CLI entry for observe_screen vision debugging."""

from __future__ import annotations

import argparse
import logging
import sys

from kokoro.core import prompts
from kokoro.action.tools.observe_screen.vision_apps import format_apps, get_foreground_app, get_running_apps
from kokoro.action.tools.observe_screen.vision_public import describe, detect_desktop

logger = logging.getLogger("vision")


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Screen capture + vision recognition")
    parser.add_argument("--prompt", "-p", default=prompts.get("vision.describe_default", "请详细描述这张图片中的内容"))
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
