"""Conversation pool for continuous STT refinement."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from typing import Callable, Optional

from kokoro import config as cfg
from kokoro import prompts

logger = logging.getLogger(__name__)


class ConversationPool:
    """Accumulates STT output and refines stable text in a single worker."""

    def __init__(
        self,
        llm_url: str = "http://127.0.0.1:11434",
        llm_model: str = "qwen2.5:0.5b",
        on_refined: Optional[Callable[[str], None]] = None,
        api_key: Optional[str] = None,
        stable_seconds: float | None = None,
        tick_seconds: float | None = None,
        max_tokens: int | None = None,
        skip_short_refine: bool | None = None,
    ):
        self.llm_url = llm_url.rstrip("/")
        self.llm_model = llm_model
        self.on_refined = on_refined
        self.api_key = api_key
        self.stable_seconds = float(stable_seconds if stable_seconds is not None else cfg.stt_refine_stable_seconds())
        self.tick_seconds = float(tick_seconds if tick_seconds is not None else cfg.stt_pool_tick_seconds())
        self.max_tokens = int(max_tokens if max_tokens is not None else cfg.stt_refine_max_tokens())
        self.skip_short_refine = bool(
            skip_short_refine if skip_short_refine is not None else cfg.stt_skip_short_refine()
        )

        self._raw = ""
        self._last_refined_text = ""
        self._last_output = ""
        self._lock = threading.Lock()
        self._running = True
        self._last_chunk_time = 0.0
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def add_chunk(self, text: str) -> None:
        if not text or not text.strip():
            return
        with self._lock:
            stripped = text.strip()
            if stripped == self._raw:
                return
            if len(stripped) < len(self._raw):
                self._last_refined_text = ""
                self._last_output = ""
            self._raw = stripped
            self._last_chunk_time = time.time()

    def stop(self) -> None:
        self._running = False

    def _get_text_to_refine(self) -> Optional[str]:
        with self._lock:
            raw = self._raw
            last_sent = self._last_refined_text
            since = time.time() - self._last_chunk_time

        if not raw or raw == last_sent or since < self.stable_seconds:
            return None
        return raw

    def _should_skip_refine(self, text: str) -> bool:
        if not self.skip_short_refine:
            return False
        stripped = text.strip()
        if len(stripped) > cfg.stt_skip_short_refine_max_chars():
            return False
        # Refine short text only when it contains obvious ASR artifacts.
        return not re.search(r"(.)\1{2,}|[?？]{2,}|[。！？!?，,、]{2,}", stripped)

    def _refine(self, text: str) -> Optional[str]:
        user_prompt = prompts.format_prompt("stt_refine.user_template", text=text)
        system_prompt = prompts.get("stt_refine.system")
        headers = {"Content-Type": "application/json"}

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            url = f"{self.llm_url}/v1/chat/completions"
            payload = {
                "model": self.llm_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": self.max_tokens,
                "thinking": {"type": "disabled"},
            }
        else:
            url = f"{self.llm_url}/api/chat"
            payload = {
                "model": self.llm_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": self.max_tokens},
            }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
            if self.api_key:
                return result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return result.get("message", {}).get("content", "").strip()
        except Exception as exc:
            logger.debug("STT refine LLM call failed: %s", exc)
            return None

    def _advance_processed(self) -> None:
        with self._lock:
            self._last_refined_text = self._raw

    def _loop(self) -> None:
        while self._running:
            text_to_refine = self._get_text_to_refine()
            if text_to_refine is None:
                time.sleep(self.tick_seconds)
                continue

            t0 = time.perf_counter()
            result = text_to_refine.strip() if self._should_skip_refine(text_to_refine) else self._refine(text_to_refine)
            elapsed = time.perf_counter() - t0
            print(f"\n  [latency] stt_refine {elapsed:.2f}s skip={result == text_to_refine.strip()}")
            if result is None:
                time.sleep(1.0)
                continue
            if result and result != self._last_output:
                self._last_output = result
                self._advance_processed()
                if self.on_refined:
                    self.on_refined(result)
            else:
                self._advance_processed()


if __name__ == "__main__":
    import sys

    def on_text(text: str) -> None:
        print(f"\n[pool] {text}")

    pool = ConversationPool(on_refined=on_text)
    try:
        while True:
            line = input()
            if line == "exit":
                break
            pool.add_chunk(line)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        pool.stop()
        sys.exit(0)
