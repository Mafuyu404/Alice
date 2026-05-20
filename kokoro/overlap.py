"""Overlap classifier — decides what to do when user speaks while AI is talking.

Uses a small local LLM (0.5B via Ollama) to classify overlapping user speech
into one of three actions.  No hard-coded thresholds — all decisions are
model-driven.

Output classes:
  CONTINUE    — User is backchanneling ("嗯", "对", "mm-hmm") or irrelevant.
                AI should keep talking.
  SOFT_BREAK  — User is starting a substantive utterance.  AI should finish
                the current sentence/phrase then yield.
  HARD_BREAK  — User is interrupting with urgency or a clear new topic.
                AI should stop immediately.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Optional

from kokoro import config as cfg
from kokoro import prompts

# Cache TTL: don't re-classify identical text within this window
_CACHE_TTL = 0.3


class OverlapClassifier:
    """Lightweight classifier for overlapping-speech scenarios.

    Usage:
        classifier = OverlapClassifier()
        action = classifier.classify(user_text="嗯", ai_context="...")
        # → "continue"
    """

    def __init__(self, model: str | None = None, ollama_url: str | None = None):
        self._model = model or cfg.overlap_model()
        self._ollama_url = (ollama_url or cfg.llm_url()).rstrip("/")
        self._lock = threading.Lock()

        # Cache: user_text → (result, timestamp)
        self._cache: dict[str, tuple[str, float]] = {}

    def classify(self, user_text: str, ai_context: str = "") -> str:
        """Classify overlapping user speech.

        Args:
            user_text: What the user said (STT partial or final).
            ai_context: What the AI was just saying (last ~100 chars).

        Returns:
            One of "continue", "soft_break", "hard_break".
        """
        text = user_text.strip()
        if not text:
            return "continue"
        if not self._model:
            return "continue"

        # Check cache
        cache_key = text
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                result, ts = cached
                if time.time() - ts < _CACHE_TTL:
                    return result

        # Call the model
        result = self._call_model(text, ai_context)
        if result is None:
            return "continue"
        if result != "hard_break":
            result = "continue"

        # Update cache
        with self._lock:
            self._cache[cache_key] = (result, time.time())
            # Trim cache periodically
            if len(self._cache) > 32:
                now = time.time()
                self._cache = {k: v for k, v in self._cache.items() if now - v[1] < 5.0}

        return result

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def _call_model(self, text: str, ai_context: str) -> Optional[str]:
        """Call the overlap classification model."""
        system_prompt = prompts.get("overlap.system")
        user_prompt = prompts.format_prompt(
            "overlap.user",
            user_text=text,
            ai_context=ai_context or "（无）",
        )
        if not system_prompt:
            return self._fallback_classify(text)

        system_prompt = (
            "你是重叠语音打断判断器。AI 正在说话，用户也开口了。\n"
            "你的任务不是判断用户有没有说话，而是判断用户是否明确要求打断 AI 当前发言。\n"
            "只有当用户的意思明确是：停下、暂停、等一下、别说了、不要继续、纠正当前正在说的内容、"
            "要求重来或强行打断时，输出 hard_break。\n"
            "以下情况一律输出 continue：附和、回答 AI 的问题、普通聊天、提新问题、继续自己的话、"
            "听不清的短片段、口头禅、背景声、意图不确定。\n"
            "如果你犹豫，输出 continue。\n"
            "只输出 hard_break 或 continue。"
        )
        user_prompt = f"AI正在说：{ai_context or '（无）'}\n\n用户说：{text}\n\n分类："

        url = f"{self._ollama_url}/api/chat"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 16},
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            raw = data.get("message", {}).get("content", "").strip().lower()
            return self._parse(raw)
        except Exception:
            return None

    def _parse(self, raw: str) -> Optional[str]:
        """Parse model output into one of the three classes."""
        for keyword in ("hard_break", "hard"):
            if keyword in raw:
                return "hard_break"
        for keyword in ("soft_break", "soft"):
            if keyword in raw:
                return "soft_break"
        if "continue" in raw:
            return "continue"
        return None

    def _fallback_classify(self, text: str) -> str:
        """Minimal fallback when no prompt is configured — still model-driven."""
        url = f"{self._ollama_url}/api/chat"
        payload = {
            "model": self._model,
            "messages": [{
                "role": "user",
                "content": prompts.format_prompt("overlap.fallback_user", text=text),
            }],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 8},
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            raw = data.get("message", {}).get("content", "").strip().lower()
            return self._parse(raw) or "continue"
        except Exception:
            return "continue"
# Module-level singleton
_classifier: Optional[OverlapClassifier] = None
_classifier_lock = threading.Lock()


def get_classifier() -> OverlapClassifier:
    global _classifier
    with _classifier_lock:
        if _classifier is None:
            _classifier = OverlapClassifier()
        return _classifier
