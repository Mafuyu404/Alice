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
import re
import time
import threading
from typing import Callable, Optional

import numpy as np

from kokoro.core import config as cfg
from kokoro.action import overlap as overlap_mod
from kokoro.core import state_machine as sm

logger = logging.getLogger(__name__)

_ECHO_TEXT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def _normalize_echo_text(text: str) -> str:
    return _ECHO_TEXT_RE.sub("", (text or "").lower())


def _echo_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    max_len = min(len(a), len(b))
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            if k > best:
                best = k
    return best / max_len if max_len else 0.0


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
        silence_endpoint_delay: float = 2.0,
        commit_delay: Optional[float] = None,
        short_extra_delay: Optional[float] = None,
        short_max_chars: Optional[int] = None,
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

        # ── silence-based endpoint ────────────────────────────────────────────
        self._silence_endpoint_delay = silence_endpoint_delay
        self._commit_delay = float(commit_delay if commit_delay is not None else cfg.stt_utterance_commit_seconds())
        self._short_extra_delay = float(
            short_extra_delay if short_extra_delay is not None else cfg.stt_short_utterance_extra_seconds()
        )
        self._short_max_chars = int(short_max_chars if short_max_chars is not None else cfg.stt_short_utterance_max_chars())
        self._silence_since: float = 0.0  # time.monotonic when text last changed
        self._last_voice_at: float = 0.0  # time.monotonic when mic energy last looked like speech
        self._had_partial = False         # True once we've seen any non-empty text
        self._last_delivery_time: float = 0.0  # cooldown to prevent double-delivery
        self._pending_text = ""
        self._pending_ready_at: float = 0.0
        self._pending_reason = ""

        # Set by cli.py after callback returns, tells cli.py which interrupt was used
        self.last_reason: str = "endpoint"

    # ── public API ──────────────────────────────────────────────────────────

    def feed_audio(self, chunk: np.ndarray) -> None:
        """Process one AEC-cleaned mic chunk."""
        with self._lock:
            now = time.monotonic()
            if self._looks_like_speech(chunk):
                self._last_voice_at = now

            self._stream.accept_waveform(self._sr, chunk)

            if not self._recognizer.is_ready(self._stream):
                self._maybe_deliver_pending(now)
                return

            self._recognizer.decode_stream(self._stream)
            text = self._recognizer.get_result(self._stream)

            if text:
                is_new = (text != self._last_partial)
                self._last_partial = text

                if is_new:
                    self._partial_count += 1
                    self._silence_since = now
                    self._had_partial = True
                    if self._on_partial:
                        self._on_partial(text)
                    self._check_overlap(text)
                    self._cancel_pending_if_continued(text)

                # Silence-based endpoint: text unchanged for delay → utterance done
                if (
                    not self._delivered
                    and len(text.strip()) >= 2
                    and self._has_real_silence(now)
                    and (now - self._silence_since) >= self._silence_endpoint_delay
                    and (now - self._last_delivery_time) >= 1.5  # cooldown: no double-delivery
                ):
                    self._stage_delivery(text, "endpoint:silence", now)
                self._maybe_deliver_pending(now)

            elif self._had_partial and not self._delivered:
                # Recognizer returned empty (e.g. stream reset internally).
                # If it's been silent long enough, deliver the last known text.
                if self._has_real_silence(now) and (now - self._silence_since) >= self._silence_endpoint_delay:
                    last = self._last_partial.strip()
                    if len(last) >= 2 and (now - self._last_delivery_time) >= 1.5:
                        self._stage_delivery(last, "endpoint:silence", now)
                self._maybe_deliver_pending(now)
            else:
                self._maybe_deliver_pending(now)

    def reset_stream(self) -> None:
        """Reset the STT stream.  Call after barge-in or utterance delivery."""
        with self._lock:
            self._stream = self._recognizer.create_stream()
            self._last_partial = ""
            self._delivered = False
            self._partial_count = 0
            self._silence_since = 0.0
            self._last_voice_at = 0.0
            self._had_partial = False
            self._last_delivery_time = 0.0
            self._pending_text = ""
            self._pending_ready_at = 0.0
            self._pending_reason = ""

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

        if self._is_ai_context_echo(text):
            print(f"\n  [trace] overlap dropped_echo text={text[:40]}")
            self._reset_after_delivery()
            return

        decision = self._classifier.classify(
            user_text=text,
            ai_context=self._ai_context,
        )
        print(f"\n  [trace] overlap decision={decision} text={text[:40]}")

        if decision == "continue":
            return

        if decision == "soft_break":
            return

        self._deliver(text, "overlap:hard_break")

    def _looks_like_speech(self, chunk: np.ndarray) -> bool:
        if chunk.size == 0:
            return False
        rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float32))))
        return rms >= 0.003

    def _has_real_silence(self, now: float) -> bool:
        if self._last_voice_at <= 0.0:
            return False
        return (now - self._last_voice_at) >= self._silence_endpoint_delay

    def _finalize_utterance(self, text: str) -> None:
        """User finished speaking — deliver if not already delivered."""
        if self._delivered:
            self._reset_after_delivery()
            return

        # Drop very short utterances — single-character endpoints are almost
        # always mid-speech pauses, not complete sentences.  The user will
        # keep talking and the next result will contain the full text.
        if len(text.strip()) < 2:
            self._reset_after_delivery()
            return

        self._deliver(text, "endpoint")

    def _deliver(self, text: str, reason: str) -> None:
        """Deliver user text to the conversation layer."""
        now = time.monotonic()
        self._delivered = True
        self._pending_text = ""
        self._pending_ready_at = 0.0
        self._pending_reason = ""
        self._last_delivery_time = now
        self.last_reason = reason
        stripped = text.strip()
        if stripped:
            since_partial = (now - self._silence_since) if self._silence_since else 0.0
            since_voice = (now - self._last_voice_at) if self._last_voice_at else 0.0
            print(
                f"\n  [trace] stt_deliver reason={reason} text={len(stripped)}ch "
                f"since_partial={since_partial:.2f}s since_voice={since_voice:.2f}s"
            )
        if stripped and self._on_user_utterance:
            self._on_user_utterance(stripped)
        self._reset_after_delivery()

    def _stage_delivery(self, text: str, reason: str, now: float) -> None:
        stripped = text.strip()
        if not stripped:
            return
        if self._machine.tts_state in (sm.TTSState.STREAMING, sm.TTSState.DRAINING):
            if self._is_ai_context_echo(stripped):
                print(f"\n  [trace] overlap_endpoint dropped_echo text={stripped[:40]}")
                self._reset_after_delivery()
                return
            decision = self._classifier.classify(
                user_text=stripped,
                ai_context=self._ai_context,
            )
            print(f"\n  [trace] overlap_endpoint decision={decision} text={stripped[:40]}")
            if decision != "hard_break":
                return
            reason = "overlap:hard_break"
        delay = self._commit_delay
        if len(stripped) <= self._short_max_chars:
            delay += self._short_extra_delay
        ready_at = now + max(0.0, delay)
        if stripped == self._pending_text:
            return
        self._pending_text = stripped
        self._pending_reason = reason
        self._pending_ready_at = ready_at
        print(
            f"\n  [trace] stt_stage reason={reason} text={len(stripped)}ch "
            f"commit_delay={delay:.2f}s endpoint_wait={self._silence_endpoint_delay:.2f}s"
        )

    def _maybe_deliver_pending(self, now: float) -> None:
        if self._delivered or not self._pending_text:
            return
        if now < self._pending_ready_at:
            return
        if not self._has_real_silence(now):
            return
        self._deliver(self._pending_text, self._pending_reason or "endpoint:silence")

    def _cancel_pending_if_continued(self, text: str) -> None:
        if not self._pending_text:
            return
        stripped = text.strip()
        if not stripped or stripped == self._pending_text:
            return
        self._pending_text = ""
        self._pending_ready_at = 0.0
        self._pending_reason = ""

    def _is_ai_context_echo(self, text: str) -> bool:
        norm = _normalize_echo_text(text)
        spoken = _normalize_echo_text(self._ai_context)
        if len(norm) < 2 or not spoken:
            return False
        if len(norm) < 8:
            return norm == spoken or spoken.startswith(norm) or spoken.endswith(norm)
        if norm in spoken or spoken in norm:
            return True
        if _echo_similarity(norm, spoken) >= cfg.stt_echo_filter_similarity():
            return True
        overlap = min(len(norm), len(spoken))
        return overlap >= 6 and (norm[:overlap] == spoken[:overlap] or norm[-overlap:] == spoken[-overlap:])

    def _reset_after_delivery(self) -> None:
        """Reset STT stream after a delivery, ready for the next utterance."""
        self._stream = self._recognizer.create_stream()
        self._last_partial = ""
        self._delivered = False
        self._partial_count = 0
        self._silence_since = 0.0
        self._last_voice_at = 0.0
        self._had_partial = False
        self._pending_text = ""
        self._pending_ready_at = 0.0
        self._pending_reason = ""
