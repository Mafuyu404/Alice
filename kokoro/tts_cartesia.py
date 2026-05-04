"""Cartesia TTS backend."""

from __future__ import annotations

import io
import logging
import queue
import threading
from typing import Generator, Optional, Tuple

import numpy as np

from kokoro import config as cfg

logger = logging.getLogger(__name__)

CARTESIA_API_KEY = cfg.cartesia_api_key()
DEFAULT_VOICE_ID = cfg.tts_voice_id() or "79eb36e0-0b79-4eac-8cb8-bb4922eb51c5"
SAMPLE_RATE = cfg.tts_sample_rate()

VOICE_PRESETS = {
    "default": DEFAULT_VOICE_ID,
    "annie": "f786b574-daa5-4673-aa0c-cbe3e8534c02",
    "scarlett": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
}

_warmed_up = False
_tts_queue: "queue.Queue[Optional[tuple[str, Optional[str], float]]]" = queue.Queue()
_tts_worker_started = False
_streaming_tts: Optional["StreamingTTS"] = None


def _resolve_voice(voice: str = None) -> str:
    if voice is None:
        return VOICE_PRESETS["default"]
    return VOICE_PRESETS.get(voice, voice)


def _get_client():
    from cartesia import Cartesia

    if not CARTESIA_API_KEY:
        raise RuntimeError("config.toml missing cartesia_api_key")
    return Cartesia(api_key=CARTESIA_API_KEY)


def text_to_speech_stream(text: str, voice: str = None, speed: float = 1.0) -> Generator[Tuple[np.ndarray, int], None, None]:
    client = _get_client()
    voice_id = _resolve_voice(voice)
    with client.tts.websocket_connect() as connection:
        for response in connection.send(
            model_id="sonic-3",
            transcript=text,
            voice={"mode": "id", "id": voice_id},
            output_format={"container": "raw", "encoding": "pcm_f32le", "sample_rate": SAMPLE_RATE},
            stream=True,
        ):
            if response.type == "chunk" and response.audio:
                audio = np.frombuffer(response.audio, dtype=np.float32)
                if len(audio) > 0:
                    yield audio, SAMPLE_RATE


def text_to_speech(text: str, voice: str = None, speed: float = 1.0) -> tuple[bytes, int]:
    import soundfile as sf

    chunks = [audio for audio, _ in text_to_speech_stream(text, voice, speed)]
    full_audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
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
        stream.write(np.concatenate(chunks))
    finally:
        stream.stop()
        stream.close()


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


class StreamingTTS:
    def __init__(self, voice: str = None):
        self._voice_id = _resolve_voice(voice)
        self._pending_buf: list[str] = []
        self._play_lock = threading.Lock()
        self._is_playing = False
        self._pending_plays = 0
        self._state_lock = threading.Lock()
        self._should_stop = False

    @property
    def is_playing(self) -> bool:
        with self._state_lock:
            return self._is_playing or self._pending_plays > 0

    def prepare(self) -> None:
        return

    def push(self, text: str) -> None:
        self._pending_buf.append(text)

    def end_sentence(self) -> None:
        if not self._pending_buf:
            return
        text = "".join(self._pending_buf)
        self._pending_buf = []
        with self._state_lock:
            self._pending_plays += 1
        threading.Thread(target=self._play_text, args=(text,), daemon=True).start()

    def flush(self) -> None:
        self.end_sentence()

    def close(self) -> None:
        self._should_stop = True

    def _play_text(self, text: str) -> None:
        try:
            if self._should_stop:
                return
            with self._play_lock:
                with self._state_lock:
                    self._is_playing = True
                chunks = [audio for audio, _ in text_to_speech_stream(text, self._voice_id, 1.0)]
                _play_audio_chunks(chunks)
        finally:
            with self._state_lock:
                self._is_playing = False
                self._pending_plays = max(0, self._pending_plays - 1)


def streaming_init(voice: str = None) -> None:
    global _streaming_tts
    streaming_close()
    _streaming_tts = StreamingTTS(voice)


def streaming_prepare() -> None:
    if _streaming_tts:
        _streaming_tts.prepare()


def streaming_push(text: str) -> None:
    if _streaming_tts:
        _streaming_tts.push(text)


def streaming_end_sentence() -> None:
    if _streaming_tts:
        _streaming_tts.end_sentence()


def streaming_flush() -> None:
    if _streaming_tts:
        _streaming_tts.flush()


def streaming_close() -> None:
    global _streaming_tts
    if _streaming_tts:
        _streaming_tts.close()
    _streaming_tts = None


def get_voices() -> dict:
    if not CARTESIA_API_KEY:
        return {"enabled": False, "voices": [], "error": "config.toml missing cartesia_api_key"}
    return {"enabled": True, "engine": "cartesia", "voices": list(VOICE_PRESETS.keys()), "voice_ids": VOICE_PRESETS}


def require_ready() -> dict:
    if not CARTESIA_API_KEY:
        raise RuntimeError("config.toml missing cartesia_api_key")
    return get_voices()


def warmup() -> None:
    global _warmed_up
    if _warmed_up:
        return
    _warmed_up = True
    if not CARTESIA_API_KEY:
        print("  Cartesia TTS: config.toml missing cartesia_api_key, skipping warmup")
