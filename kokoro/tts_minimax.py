"""MiniMax TTS backend."""

from __future__ import annotations

import io
import json
import logging
import queue
import re
import threading
import time
from typing import Generator, Optional, Tuple

import numpy as np
import sounddevice as sd

from kokoro import config as cfg

logger = logging.getLogger(__name__)

MINIMAX_API_KEY = cfg.minimax_api_key()
MINIMAX_MODEL = cfg.minimax_model()
SAMPLE_RATE = int(cfg.get("minimax_sample_rate", 32000))
WS_URL = "wss://api.minimax.io/ws/v1/t2a_v2"

VOICE_PRESETS = {
    "default": "Chinese (Mandarin)_Crisp_Girl",
    "crisp_girl": "Chinese (Mandarin)_Crisp_Girl",
    "qingse": "male-qn-qingse",
    "tianmei": "female-tianmei",
    "narrator_en": "English_expressive_narrator",
}

_warmed_up = False
_tts_queue: "queue.Queue[Optional[tuple[str, Optional[str], float]]]" = queue.Queue()
_tts_worker_started = False
_streaming_tts: Optional["StreamingTTS"] = None


def _ws_headers() -> dict:
    return {"Authorization": f"Bearer {MINIMAX_API_KEY}"}


def _task_start(voice_id: str, speed: float) -> dict:
    return {
        "event": "task_start",
        "model": MINIMAX_MODEL,
        "voice_setting": {"voice_id": voice_id, "speed": speed, "vol": 1.0, "pitch": 0},
        "audio_setting": {"sample_rate": SAMPLE_RATE, "format": "pcm", "channel": 1},
    }


def _task_continue(text: str) -> dict:
    return {"event": "task_continue", "text": text}


def _resolve_voice(voice: str = None) -> str:
    if voice is None:
        return VOICE_PRESETS["default"]
    return VOICE_PRESETS.get(voice, voice)


def _decode_audio_chunk(data: dict) -> Optional[np.ndarray]:
    audio_hex = data.get("audio", "")
    if not audio_hex:
        return None
    try:
        raw = bytes.fromhex(audio_hex)
    except Exception:
        return None
    if len(raw) % 2:
        raw = raw[:-1]
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _send_and_receive_stream(text: str, voice_id: str, speed: float) -> Generator[np.ndarray, None, None]:
    import websockets.sync.client as ws_sync
    from websockets.exceptions import ConnectionClosed

    with ws_sync.connect(WS_URL, additional_headers=_ws_headers()) as ws:
        ws.send(json.dumps(_task_start(voice_id, speed)))
        while True:
            try:
                msg = ws.recv(timeout=10)
            except (ConnectionClosed, TimeoutError):
                break
            if isinstance(msg, bytes):
                continue
            data = json.loads(msg)
            event = data.get("event", "")
            if event == "task_started":
                ws.send(json.dumps(_task_continue(text)))
            elif event == "task_continued":
                payload = data.get("data", {})
                audio = _decode_audio_chunk(payload)
                if audio is not None and len(audio) > 0:
                    yield audio
                if data.get("is_final") or payload.get("is_final"):
                    break
            elif event in ("task_finished", "task_failed"):
                break
        try:
            ws.send(json.dumps({"event": "task_finish"}))
        except Exception:
            pass


def text_to_speech_stream(text: str, voice: str = None, speed: float = 1.0) -> Generator[Tuple[np.ndarray, int], None, None]:
    for audio in _send_and_receive_stream(text, _resolve_voice(voice), speed):
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


_SENTENCE_END = re.compile(r"[。！？!?；;]")

class StreamingTTS:
    """Streaming TTS with persistent WebSocket and automatic reconnect.

    push() accumulates text. When a sentence-ending punctuation is detected,
    the complete sentence is sent via task_continue.  If the WebSocket drops,
    the sentence stays in the buffer and the receiver reconnects transparently.
    """

    def __init__(self, voice: str = None):
        self._voice_id = _resolve_voice(voice)
        self._speed = float(cfg.get("minimax_tts_speed", 1.0))
        self._buffer_samples = int(SAMPLE_RATE * float(cfg.get("minimax_tts_buffer_seconds", 0.3)))
        self._buf: list[str] = []
        self._is_playing = False
        self._should_stop = False
        self._state_lock = threading.Lock()
        self._first_audio_logged = False
        self._audio_queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self._play_thread: threading.Thread | None = None
        self._stream: sd.OutputStream | None = None
        self._ws: object = None
        self._ws_started = threading.Event()
        self._ws_recv_thread: threading.Thread | None = None
        self._pending_count = 0
        self._pending_lock = threading.Lock()
        self._all_done = threading.Event()
        self._all_done.set()
        self._session_done = False

    @property
    def is_playing(self) -> bool:
        with self._state_lock:
            return self._is_playing or not self._audio_queue.empty()

    def prepare(self) -> None:
        """Start audio output and the WS receiver thread (manages its own connection)."""
        if self._stream is None:
            self._stream = sd.OutputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=2048,
            )
            self._stream.start()
        if self._play_thread is None or not self._play_thread.is_alive():
            self._play_thread = threading.Thread(target=self._play_worker, daemon=True)
            self._play_thread.start()

        self._all_done.set()
        self._session_done = False
        with self._pending_lock:
            self._pending_count = 0
        self._ws_recv_thread = threading.Thread(target=self._ws_recv_worker, daemon=True)
        self._ws_recv_thread.start()
        if not self._ws_started.wait(timeout=10):
            raise RuntimeError("MiniMax TTS task_started timeout")

    def _try_send(self, text: str) -> bool:
        """Send a sentence. Returns False if WS is down (caller should retry)."""
        if not self._ws_started.is_set() or not self._ws:
            return False
        try:
            with self._pending_lock:
                self._pending_count += 1
                self._all_done.clear()
            self._ws.send(json.dumps(_task_continue(text)))
            return True
        except Exception as exc:
            logger.warning("TTS send failed: %s", exc)
            with self._pending_lock:
                self._pending_count = max(0, self._pending_count - 1)
                if self._pending_count == 0:
                    self._all_done.set()
            self._ws_started.clear()
            return False

    def push(self, text: str) -> None:
        """Accumulate text; send complete sentences when WS is available."""
        self._buf.append(text)
        while True:
            combined = "".join(self._buf)
            match = _SENTENCE_END.search(combined)
            if not match:
                break
            idx = match.end()
            sentence = combined[:idx]
            stripped = sentence.strip()
            if stripped and self._try_send(stripped):
                rest = combined[idx:]
                self._buf = [rest] if rest else []
            else:
                break  # WS down, retry on next push() or end_sentence()

    def end_sentence(self) -> None:
        """Flush remaining text, then finish the session."""
        # Keep trying to send remaining text until WS is available and it goes through
        deadline = time.perf_counter() + 30
        while True:
            remaining = "".join(self._buf).strip()
            if not remaining:
                break
            if self._try_send(remaining):
                self._buf = []
                break
            if time.perf_counter() > deadline or self._should_stop:
                self._buf = []
                break
            time.sleep(0.1)

        # Wait for all pending sentences to finish
        self._all_done.wait(timeout=30)
        self._session_done = True
        if self._ws and self._ws_started.is_set():
            try:
                self._ws.send(json.dumps({"event": "task_finish"}))
            except Exception:
                pass

    def flush(self) -> None:
        self.end_sentence()

    def close(self) -> None:
        self._should_stop = True
        self._session_done = True
        self._audio_queue.put(None)
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def interrupt(self) -> None:
        """Stop playback immediately and reset state for a new session."""
        self._should_stop = True
        self._session_done = True
        self._buf = []
        with self._pending_lock:
            self._pending_count = 0
            self._all_done.set()
        # Drain audio queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        self._audio_queue.put(None)
        self._ws_started.clear()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._state_lock:
            self._is_playing = False
        self._should_stop = False
        self._session_done = False
        self._first_audio_logged = False

    def _ws_recv_worker(self) -> None:
        """Receiver loop. Reconnects automatically on unexpected drops."""
        import websockets.sync.client as ws_sync
        from websockets.exceptions import ConnectionClosed

        while not self._should_stop:
            self._ws_started.clear()
            try:
                self._ws = ws_sync.connect(WS_URL, additional_headers=_ws_headers())
                self._ws.send(json.dumps(_task_start(self._voice_id, self._speed)))
            except Exception:
                if self._should_stop:
                    return
                time.sleep(0.5)
                continue

            t0 = time.perf_counter()
            try:
                while not self._should_stop:
                    try:
                        msg = self._ws.recv(timeout=1)
                    except TimeoutError:
                        if self._session_done:
                            return
                        continue
                    except ConnectionClosed:
                        break  # Will reconnect below

                    if isinstance(msg, bytes):
                        continue
                    data = json.loads(msg)
                    event = data.get("event", "")

                    if event == "task_started":
                        self._ws_started.set()
                    elif event == "task_continued":
                        if not self._first_audio_logged:
                            self._first_audio_logged = True
                            print(f"\n  [latency] tts_first_audio {time.perf_counter() - t0:.2f}s")
                        audio = _decode_audio_chunk(data.get("data", {}))
                        if audio is not None and len(audio) > 0:
                            self._audio_queue.put(audio)
                        if data.get("is_final") or data.get("data", {}).get("is_final"):
                            with self._pending_lock:
                                self._pending_count = max(0, self._pending_count - 1)
                                if self._pending_count == 0:
                                    self._all_done.set()
                    elif event in ("task_finished", "task_failed"):
                        self._audio_queue.put(None)
                        self._ws_started.clear()
                        return
            except Exception:
                pass
            finally:
                self._ws_started.clear()
                # Reset pending on unexpected disconnect — lost sentence audio
                # will be compensated by re-sending from buffer on the new connection.
                with self._pending_lock:
                    self._pending_count = 0
                    self._all_done.set()
                if self._ws:
                    try:
                        self._ws.close()
                    except Exception:
                        pass
                    self._ws = None

            if self._should_stop or self._session_done:
                return
            time.sleep(0.2)

    def _play_worker(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            prebuf: list[np.ndarray] = []
            prebuf_samples = 0
            started = False
            while not self._should_stop:
                try:
                    audio = self._audio_queue.get(timeout=0.15)
                except queue.Empty:
                    if started:
                        with self._state_lock:
                            self._is_playing = False
                    continue

                if audio is None:
                    if prebuf:
                        for chunk in prebuf:
                            stream.write(chunk)
                        prebuf = []
                    started = False
                    prebuf_samples = 0
                    with self._state_lock:
                        self._is_playing = False
                    continue
                if not started:
                    prebuf.append(audio)
                    prebuf_samples += len(audio)
                    if prebuf_samples >= self._buffer_samples:
                        with self._state_lock:
                            self._is_playing = True
                        started = True
                        for chunk in prebuf:
                            stream.write(chunk)
                        prebuf = []
                    continue
                with self._state_lock:
                    self._is_playing = True
                stream.write(audio)
        finally:
            with self._state_lock:
                self._is_playing = False


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
    if not MINIMAX_API_KEY:
        return {"enabled": False, "voices": [], "error": "config.toml missing minimax_api_key"}
    return {"enabled": True, "engine": "minimax", "voices": list(VOICE_PRESETS.keys()), "voice_ids": VOICE_PRESETS}


def require_ready() -> dict:
    if not MINIMAX_API_KEY:
        raise RuntimeError("config.toml missing minimax_api_key")
    return get_voices()


def warmup() -> None:
    global _warmed_up
    if _warmed_up:
        return
    _warmed_up = True
    if not MINIMAX_API_KEY:
        print("  MiniMax TTS: config.toml missing minimax_api_key, skipping warmup")
