"""Acoustic echo cancellation support for the speech tool."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np
from aec_audio_processing import AudioProcessor

from kokoro.core import config as cfg


@dataclass(frozen=True)
class AECInstallResult:
    processor: "AECProcessor | None"
    message: str


class AECProcessor:
    """WebRTC AEC + noise suppression for microphone chunks."""

    def __init__(
        self,
        mic_sample_rate: int = 16000,
        tts_sample_rate: int = 32000,
        ns_level: int = 2,
    ):
        self._mic_sr = mic_sample_rate
        self._tts_sr = tts_sample_rate
        self._ns_level = ns_level
        self._delay_ms = 50
        self._lock = threading.Lock()
        self._frame_size = mic_sample_rate // 100
        self._init_processor()
        self._mic_buf: np.ndarray = np.array([], dtype=np.float32)
        self._ref_buf: np.ndarray = np.array([], dtype=np.float32)
        self.on_mic_frame: Callable[[np.ndarray], None] | None = None
        self.on_cleaned_frame: Callable[[np.ndarray], None] | None = None

    def _init_processor(self) -> None:
        self._processor = AudioProcessor(
            enable_aec=True,
            enable_ns=True,
            ns_level=self._ns_level,
            enable_agc=False,
            enable_vad=False,
        )
        self._processor.set_stream_format(self._mic_sr, 1)
        self._processor.set_reverse_stream_format(self._mic_sr, 1)
        self._processor.set_stream_delay(self._delay_ms)

    def set_delay(self, delay_ms: int) -> None:
        self._delay_ms = delay_ms
        self._processor.set_stream_delay(delay_ms)

    def push_reference(self, audio: np.ndarray) -> None:
        if len(audio) == 0:
            return
        if self._tts_sr != self._mic_sr:
            audio = _resample(audio, self._tts_sr, self._mic_sr)
        with self._lock:
            self._ref_buf = np.concatenate([self._ref_buf, audio.ravel()])
            self._drain_ref()

    def process(self, mic_audio: np.ndarray) -> np.ndarray:
        if len(mic_audio) == 0:
            return np.array([], dtype=np.float32)

        with self._lock:
            self._drain_ref()
            self._mic_buf = np.concatenate([self._mic_buf, mic_audio.ravel()])
            out_chunks: list[np.ndarray] = []

            while len(self._mic_buf) >= self._frame_size:
                frame = self._mic_buf[:self._frame_size]
                self._mic_buf = self._mic_buf[self._frame_size:]
                if self.on_mic_frame:
                    self.on_mic_frame(frame)
                cleaned = _s16_to_f32(self._processor.process_stream(_f32_to_s16(frame)))
                if self.on_cleaned_frame:
                    self.on_cleaned_frame(cleaned)
                out_chunks.append(cleaned)

        if out_chunks:
            return np.concatenate(out_chunks)
        return np.array([], dtype=np.float32)

    def flush(self) -> np.ndarray:
        with self._lock:
            if len(self._mic_buf) == 0:
                return np.array([], dtype=np.float32)
            needed = self._frame_size - len(self._mic_buf)
            frame = np.concatenate([self._mic_buf, np.zeros(needed, dtype=np.float32)])
            self._mic_buf = np.array([], dtype=np.float32)
            self._ref_buf = np.array([], dtype=np.float32)
            return _s16_to_f32(self._processor.process_stream(_f32_to_s16(frame)))

    def reset(self) -> None:
        with self._lock:
            self._mic_buf = np.array([], dtype=np.float32)
            self._ref_buf = np.array([], dtype=np.float32)
            self._init_processor()

    def _drain_ref(self) -> None:
        while len(self._ref_buf) >= self._frame_size:
            frame = self._ref_buf[:self._frame_size]
            self._ref_buf = self._ref_buf[self._frame_size:]
            self._processor.process_reverse_stream(_f32_to_s16(frame))


def install_aec(
    *,
    enabled: bool,
    tts_engines: Iterable[object],
    mic_sample_rate: int,
    tts_sample_rate: int,
    printer: Callable[[str], None] = print,
) -> AECInstallResult:
    engines = [engine for engine in tts_engines if engine is not None]
    if not enabled or not engines:
        return AECInstallResult(None, "disabled")
    try:
        processor = AECProcessor(
            mic_sample_rate=mic_sample_rate,
            tts_sample_rate=tts_sample_rate,
            ns_level=cfg.aec_ns_level(),
        )
        processor.set_delay(cfg.aec_delay_ms())
        for engine in engines:
            engine.on_audio_frame = processor.push_reference
        message = (
            f"enabled (playback_sr={tts_sample_rate}, "
            f"mic_sr={mic_sample_rate}, delay={cfg.aec_delay_ms()}ms)"
        )
        printer(f"  [aec] {message}")
        return AECInstallResult(processor, message)
    except Exception as exc:
        message = f"init failed: {exc}"
        printer(f"  [aec] {message}")
        return AECInstallResult(None, message)


def install_default_aec(
    *,
    enabled: bool,
    tts_engines: Iterable[object],
    printer: Callable[[str], None] = print,
) -> AECInstallResult:
    from kokoro.action.tools import say as say_tool
    from kokoro.action.tools import speech_input as speech_input_tool

    return install_aec(
        enabled=enabled,
        tts_engines=tts_engines,
        mic_sample_rate=speech_input_tool.SAMPLE_RATE,
        tts_sample_rate=say_tool.SAMPLE_RATE,
        printer=printer,
    )


def _f32_to_s16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def _s16_to_f32(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    src_len = len(audio)
    if src_len == 0:
        return audio
    dst_len = max(1, int(round(src_len * dst_rate / src_rate)))
    if dst_len == src_len:
        return audio
    indices = np.linspace(0, src_len - 1, dst_len)
    x = np.arange(src_len)
    return np.interp(indices, x, audio).astype(np.float32)
