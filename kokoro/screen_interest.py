"""Screen interest detection for proactive dialogue context.

Instead of a generic "is this interesting?" check, this module analyzes the
foreground window content — extracting readable text and describing the visual
context — so the companion can naturally comment, ask, correct, or react to
what the user is actually doing.

A module-level ScreenCache holds the latest analysis result for zero-cost reads.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass

from kokoro import prompts
from kokoro import vision


logger = logging.getLogger(__name__)

PRIVACY_PATTERNS = (
    "password",
    "passwd",
    "login",
    "sign in",
    "signin",
    "bank",
    "payment",
    "checkout",
    "wallet",
    "authenticator",
    "2fa",
    "private browsing",
    "incognito",
    "隐私",
    "密码",
    "登录",
    "登陆",
    "支付",
    "付款",
    "银行",
    "验证码",
    "会议",
    "meeting",
    "zoom",
    "teams",
    "tencentmeeting",
)


@dataclass(frozen=True)
class ScreenInterest:
    score: float
    content: str
    reason: str = ""
    private: bool = False


def foreground_is_private(foreground: dict | None) -> bool:
    if not foreground:
        return False
    text = " ".join(
        str(foreground.get(key, ""))
        for key in ("title", "process", "class_name")
    ).lower()
    return any(pattern in text for pattern in PRIVACY_PATTERNS)


def analyze(
    *,
    backend: str | None = None,
    model: str | None = None,
    timeout: int = 45,
) -> ScreenInterest:
    foreground = vision.get_foreground_app()
    if foreground_is_private(foreground):
        return ScreenInterest(
            score=0.0, content="", reason="foreground privacy guard", private=True,
        )

    image_uri = vision.screenshot_to_base64()
    prompt = _content_prompt(foreground)
    raw = vision.analyze_image(image_uri, prompt, backend=backend, model=model, timeout=timeout, function="screen_interest")
    return _parse_content(raw)


def _content_prompt(foreground: dict | None) -> str:
    fg_title = (foreground or {}).get("title", "")
    fg_proc = (foreground or {}).get("process", "")
    fg_info = ""
    if fg_title:
        fg_info = (
            f"前台窗口标题：{fg_title}\n"
            f"前台窗口进程：{fg_proc}\n\n"
        )
    return prompts.format_prompt("screen_interest.content_analysis", fg_info=fg_info)


def _parse_content(text: str) -> ScreenInterest:
    data = _extract_json(text)
    if isinstance(data, dict):
        private = bool(data.get("private", False))
        try:
            score = float(data.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        content = str(data.get("content", "")).strip()[:600]
        reason = str(data.get("reason", "")).strip()[:200]
        return ScreenInterest(
            score=_clamp(score, 0.0, 100.0),
            content=content,
            reason=reason,
            private=private,
        )

    # Fallback: raw text with zero score
    return ScreenInterest(
        score=0.0,
        content=text.strip()[:400],
        reason="unstructured vision response",
        private=False,
    )


def _extract_json(text: str) -> dict | None:
    """Try multiple strategies to extract a JSON dict from the model response."""
    stripped = text.strip()

    # Strategy 1: extract from markdown code block
    code_match = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
    if code_match:
        candidate = code_match.group(1).strip()
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass

    # Strategy 2: find the outermost balanced braces
    depth = 0
    start = -1
    for i, ch in enumerate(stripped):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = stripped[start : i + 1]
                try:
                    value = json.loads(candidate)
                    if isinstance(value, dict):
                        return value
                except json.JSONDecodeError:
                    # Try cleaning up common issues
                    cleaned = _clean_json(candidate)
                    if cleaned != candidate:
                        try:
                            value = json.loads(cleaned)
                            if isinstance(value, dict):
                                return value
                        except json.JSONDecodeError:
                            pass

    logger.debug("_extract_json: no valid JSON found in model response (len=%d)", len(stripped))
    return None


def _clean_json(text: str) -> str:
    """Remove trailing commas before closing braces/brackets."""
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    return text


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


# ═══════════════════════════════════════════════════════════════════════════════
# ScreenCache — thread-safe cache for continuous screen analysis
# ═══════════════════════════════════════════════════════════════════════════════

class ScreenCache:
    """Thread-safe cache holding the latest screen analysis result."""

    def __init__(self):
        self._lock = threading.Lock()
        self._latest: ScreenInterest | None = None
        self._timestamp: float = 0.0

    def put(self, result: ScreenInterest) -> None:
        with self._lock:
            self._latest = result
            self._timestamp = time.time()

    def get(self) -> tuple[ScreenInterest | None, float]:
        with self._lock:
            return self._latest, self._timestamp

    def content(self) -> str:
        with self._lock:
            return (self._latest.content or "") if self._latest else ""

    def score(self) -> float:
        with self._lock:
            return self._latest.score if self._latest else 0.0


# Module-level singleton
_SCREEN_CACHE = ScreenCache()


def get_cache() -> ScreenCache:
    return _SCREEN_CACHE
