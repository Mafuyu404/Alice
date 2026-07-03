"""One-shot MiniMax TTS synthesis and playback helpers."""

from __future__ import annotations

import io
import queue
import threading
from typing import Generator, Optional, Tuple

import numpy as np

from kokoro.core import token_usage
from kokoro.action.tools.say.tts_minimax_config import (
    MINIMAX_MODEL,
    SAMPLE_RATE,
    TTS_VOLUME,
    resolve_voice,
)
from kokoro.action.tools.say.tts_minimax_protocol import send_and_receive_stream

_tts_queue: "queue.Queue[Optional[tuple[str, Optional[str], float]]]" = queue.Queue()
_tts_worker_started = False


def text_to_speech_stream(text: str, voice: str = None, speed: float = 1.0) -> Generator[Tuple[np.ndarray, int], None, None]:
    cc = len(text)
    if cc:
        token_usage.record(MINIMAX_MODEL, "tts", cc, 0)
    for audio in send_and_receive_stream(text, resolve_voice(voice), speed):
        yield audio, SAMPLE_RATE


def text_to_speech(text: str, voice: str = None, speed: float = 1.0) -> tuple[bytes, int]:
    import soundfile as sf

    chunks = [audio for audio, _ in text_to_speech_stream(text, voice, speed)]
    if chunks:
        full_audio = np.concatenate(chunks)
    else:
        full_audio = np.zeros(1, dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, full_audio, SAMPLE_RATE, format="WAV")
    return buf.getvalue(), SAMPLE_RATE


def _play_audio_chunks(chunks: list[np.ndarray]) -> None:
    if not chunks:
        return
    import sounddevice as sd

    stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=2048)
    stream.start()
    try:
        stream.write(_apply_volume(np.concatenate(chunks)))
    finally:
        stream.stop()
        stream.close()


def _apply_volume(audio: np.ndarray) -> np.ndarray:
    if TTS_VOLUME == 1.0:
        return audio
    return np.clip(audio * TTS_VOLUME, -1.0, 1.0).astype(np.float32, copy=False)


def play_tts(text: str, voice: str = None, speed: float = 1.0, blocking: bool = True):
    def run() -> None:
        chunks = [audio for audio, _ in text_to_speech_stream(text, voice, speed)]
        _play_audio_chunks(chunks)

    if blocking:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()


def _tts_worker() -> None:
    while True:
        item = _tts_queue.get()
        if item is None:
            _tts_queue.task_done()
            break
        text, voice, speed = item
        try:
            play_tts(text, voice, speed, blocking=True)
        finally:
            _tts_queue.task_done()


def enqueue_tts(text: str, voice: str = None, speed: float = 1.0) -> None:
    global _tts_worker_started
    if not _tts_worker_started:
        _tts_worker_started = True
        threading.Thread(target=_tts_worker, daemon=True).start()
    _tts_queue.put((text, voice, speed))


def stop_playback() -> None:
    pass
