"""ConversationManager — natural dialogue orchestration.

Replaces the old ConversationPool with an event-driven system that supports:
  - Real-time STT partial text processing
  - Overlap detection: when user speaks while AI speaks, classify intent
  - Three interrupt levels: continue / soft_break / hard_break
  - Backchannel suppression (via the overlap classifier, no hard rules)

This module owns the STT side only.  When a user utterance is ready (either
because the classifier decided to break in, or the user finished speaking),
it calls ``on_user_utterance(text)`` — the CLI layer then handles LLM dispatch.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import numpy as np

from kokoro import config as cfg
from kokoro import overlap as overlap_mod
from kokoro import state_machine as sm

logger = logging.getLogger(__name__)


class ConversationManager:
    """Processes mic audio through STT and produces user-utterance events.

    Thread-safe: ``feed_audio()`` is re-entrant, called from the STT audio
    thread.  Callbacks run in the caller's thread.
    """

    def __init__(
        self,
        recognizer,           # sherpa-onnx OnlineRecognizer
        machine: sm.SystemStateMachine,
        *,
        on_user_utterance: Optional[Callable[[str], None]] = None,
        on_partial: Optional[Callable[[str], None]] = None,
        sample_rate: int = 16000,
    ):
        self._recognizer = recognizer
        self._machine = machine
        self._sr = sample_rate
        self._on_user_utterance = on_user_utterance
        self._on_partial = on_partial

        self._classifier = overlap_mod.get_classifier()
        self._lock = threading.RLock()  # RLock so reset_stream() can be called from within callbacks

        self._stream = recognizer.create_stream()
        self._last_partial = ""

        self._delivered = False
        self._ai_context = ""
        self._partial_count = 0  # Partial updates in current utterance

        # Set by cli.py after callback returns, tells cli.py which interrupt was used
        self.last_reason: str = "endpoint"

    # ── public API ──────────────────────────────────────────────────────────

    def feed_audio(self, chunk: np.ndarray) -> None:
        """Process one AEC-cleaned mic chunk."""
        with self._lock:
            self._stream.accept_waveform(self._sr, chunk)

            if not self._recognizer.is_ready(self._stream):
                return

            self._recognizer.decode_stream(self._stream)
            text = self._recognizer.get_result(self._stream)
            if not text:
                return

            is_new = (text != self._last_partial)
            self._last_partial = text

            if is_new:
                self._partial_count += 1
                if self._on_partial:
                    self._on_partial(text)
                self._check_overlap(text)

            if self._recognizer.is_endpoint(self._stream):
                self._finalize_utterance(text)
                return

    def reset_stream(self) -> None:
        """Reset the STT stream.  Call after barge-in or utterance delivery."""
        with self._lock:
            self._stream = self._recognizer.create_stream()
            self._last_partial = ""
            self._delivered = False
            self._partial_count = 0

    def update_ai_context(self, text: str) -> None:
        """Feed back what the AI is currently saying (for overlap context)."""
        self._ai_context = text[-200:] if len(text) > 200 else text

    def clear_ai_context(self) -> None:
        self._ai_context = ""

    # ── internal ────────────────────────────────────────────────────────────

    def _check_overlap(self, text: str) -> None:
        """If the AI is speaking, classify the overlap and act on it."""
        if self._delivered:
            return

        if self._machine.tts_state not in (sm.TTSState.STREAMING, sm.TTSState.DRAINING):
            return

        # Debounce: wait for at least 2 partial updates before acting.
        # The very first partial (e.g. "啊") is too short and unreliable for
        # any classifier — by the second update we have real context ("啊我...").
        if self._partial_count < 2:
            return

        decision = self._classifier.classify(
            user_text=text,
            ai_context=self._ai_context,
        )

        if decision == "continue":
            return

        if decision == "soft_break":
            self._deliver(text, "overlap:soft_break")
            return

        self._deliver(text, "overlap:hard_break")

    def _finalize_utterance(self, text: str) -> None:
        """User finished speaking — deliver if not already delivered."""
        if self._delivered:
            self._reset_after_delivery()
            return

        self._deliver(text, "endpoint")

    def _deliver(self, text: str, reason: str) -> None:
        """Deliver user text to the conversation layer."""
        self._delivered = True
        self.last_reason = reason
        stripped = text.strip()
        if stripped and self._on_user_utterance:
            self._on_user_utterance(stripped)

    def _reset_after_delivery(self) -> None:
        """Reset STT stream after a delivery, ready for the next utterance."""
        self._stream = self._recognizer.create_stream()
        self._last_partial = ""
        self._delivered = False
