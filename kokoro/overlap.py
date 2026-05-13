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
            return "hard_break"

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
            return "hard_break"  # Fallback: assume interruption

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
            return self._parse(raw) or "hard_break"
        except Exception:
            return "hard_break"


# Module-level singleton
_classifier: Optional[OverlapClassifier] = None
_classifier_lock = threading.Lock()


def get_classifier() -> OverlapClassifier:
    global _classifier
    with _classifier_lock:
        if _classifier is None:
            _classifier = OverlapClassifier()
        return _classifier
