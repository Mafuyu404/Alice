"""
AEC — Acoustic Echo Cancellation for the voice pipeline.

Wraps aec_audio_processing (WebRTC AudioProcessing) to cancel TTS playback
echo from microphone input.  The AEC needs two signals:

  - **Near-end (stream):** microphone input that may contain echo
  - **Far-end / reverse (reverse stream):** the TTS audio being played through
    speakers — used as a reference to subtract the echo

The underlying WebRTC library operates on **exactly one 10 ms frame at a time**
of int16 PCM bytes.  This module handles float32 ↔ int16 conversion, frame
buffering, and sample-rate resampling transparently.
"""

from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np
from aec_audio_processing import AudioProcessor


class AECProcessor:
    """Acoustic echo cancellation using WebRTC AEC + NS.

    Callers pass float32 arrays of arbitrary length.  Internal 10 ms frame
    buffering and int16 conversion are handled automatically.

    Usage:
        aec = AECProcessor(mic_sample_rate=16000, tts_sample_rate=32000)
        aec.set_delay(50)

        # Feed TTS playback audio as far-end reference
        tts_engine.on_audio_frame = aec.push_reference

        # Process each mic chunk → echo-cancelled output
        cleaned = aec.process(mic_chunk)
    """

    def __init__(
        self,
        mic_sample_rate: int = 16000,
        tts_sample_rate: int = 32000,
        ns_level: int = 2,
    ):
        """
        Args:
            mic_sample_rate: Microphone capture sample rate (Hz).
            tts_sample_rate: TTS playback sample rate (Hz).  Audio passed to
                ``push_reference`` is assumed to be at this rate.
            ns_level: Noise suppression level (0 = off, 1 = mild, 2 = moderate,
                      3 = aggressive, 4 = very aggressive).
        """
        # Both streams run at the mic rate internally.  TTS audio is resampled
        # to match if needed.
        self._mic_sr = mic_sample_rate
        self._tts_sr = tts_sample_rate

        self._processor = AudioProcessor(
            enable_aec=True,
            enable_ns=True,
            ns_level=ns_level,
            enable_agc=False,
            enable_vad=False,
        )
        self._processor.set_stream_format(mic_sample_rate, 1)
        self._processor.set_reverse_stream_format(mic_sample_rate, 1)

        # 10 ms frame in samples
        self._frame_size = mic_sample_rate // 100
        self._processor.set_stream_delay(50)

        # Internal float32 buffers for partial frames
        self._mic_buf: np.ndarray = np.array([], dtype=np.float32)
        self._ref_buf: np.ndarray = np.array([], dtype=np.float32)

        # Debug hooks
        self.on_mic_frame: Optional[Callable[[np.ndarray], None]] = None
        self.on_cleaned_frame: Optional[Callable[[np.ndarray], None]] = None

    # ── public API ──────────────────────────────────────────────────────────

    def set_delay(self, delay_ms: int) -> None:
        """Set the estimated capture-to-render delay in milliseconds."""
        self._processor.set_stream_delay(delay_ms)

    def push_reference(self, audio: np.ndarray) -> None:
        """Feed TTS playback audio as the AEC far-end reference signal.

        ``audio`` must be a 1-D float32 array in [-1, 1] at the
        ``tts_sample_rate`` passed at construction time.
        """
        if len(audio) == 0:
            return
        # Resample to mic rate if rates differ
        if self._tts_sr != self._mic_sr:
            audio = _resample(audio, self._tts_sr, self._mic_sr)
        self._ref_buf = np.concatenate([self._ref_buf, audio.ravel()])
        self._drain_ref()

    def process(self, mic_audio: np.ndarray) -> np.ndarray:
        """Process microphone audio with AEC + noise suppression.

        Args:
            mic_audio: 1-D float32 array in [-1, 1] at ``mic_sample_rate``.

        Returns:
            Echo-cancelled + noise-suppressed audio (same shape/rate).
        """
        if len(mic_audio) == 0:
            return np.array([], dtype=np.float32)

        # Drain pending reference so the AEC has the most recent far-end signal
        self._drain_ref()

        self._mic_buf = np.concatenate([self._mic_buf, mic_audio.ravel()])
        out_chunks: list[np.ndarray] = []

        while len(self._mic_buf) >= self._frame_size:
            frame = self._mic_buf[:self._frame_size]
            self._mic_buf = self._mic_buf[self._frame_size:]

            if self.on_mic_frame:
                self.on_mic_frame(frame)

            # float32 [-1,1] → int16 bytes → WebRTC AEC → int16 bytes → float32
            frame_bytes = _f32_to_s16(frame)
            cleaned_bytes = self._processor.process_stream(frame_bytes)
            cleaned = _s16_to_f32(cleaned_bytes)

            if self.on_cleaned_frame:
                self.on_cleaned_frame(cleaned)

            out_chunks.append(cleaned)

        if out_chunks:
            return np.concatenate(out_chunks)
        return np.array([], dtype=np.float32)

    def flush(self) -> np.ndarray:
        """Flush any remaining buffered mic audio (zero-padded to a frame)."""
        if len(self._mic_buf) == 0:
            return np.array([], dtype=np.float32)
        needed = self._frame_size - len(self._mic_buf)
        frame = np.concatenate(
            [self._mic_buf, np.zeros(needed, dtype=np.float32)]
        )
        self._mic_buf = np.array([], dtype=np.float32)
        self._ref_buf = np.array([], dtype=np.float32)
        frame_bytes = _f32_to_s16(frame)
        return _s16_to_f32(self._processor.process_stream(frame_bytes))

    def reset(self) -> None:
        """Clear internal buffers.  Call on TTS interrupt / barge-in."""
        self._mic_buf = np.array([], dtype=np.float32)
        self._ref_buf = np.array([], dtype=np.float32)

    # ── internal ────────────────────────────────────────────────────────────

    def _drain_ref(self) -> None:
        while len(self._ref_buf) >= self._frame_size:
            frame = self._ref_buf[:self._frame_size]
            self._ref_buf = self._ref_buf[self._frame_size:]
            self._processor.process_reverse_stream(_f32_to_s16(frame))


# ═══════════════════════════════════════════════════════════════════════════════
# Conversion helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _f32_to_s16(audio: np.ndarray) -> bytes:
    """float32 [-1, 1] → int16 PCM bytes."""
    return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def _s16_to_f32(data: bytes) -> np.ndarray:
    """int16 PCM bytes → float32 [-1, 1] array."""
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Resample 1-D float32 audio using linear interpolation."""
    src_len = len(audio)
    if src_len == 0:
        return audio
    dst_len = max(1, int(round(src_len * dst_rate / src_rate)))
    if dst_len == src_len:
        return audio
    # numpy interp is fast enough for real-time use at these sizes
    indices = np.linspace(0, src_len - 1, dst_len)
    x = np.arange(src_len)
    return np.interp(indices, x, audio).astype(np.float32)
