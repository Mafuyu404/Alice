"""MiniMax TTS backend."""

from __future__ import annotations

import io
import json
import logging
import queue
import re
import threading
import time
from collections import deque
from typing import Callable, Generator, Optional, Tuple

import numpy as np
import sounddevice as sd

from kokoro.core import config as cfg
from kokoro.core import token_usage

logger = logging.getLogger(__name__)

MINIMAX_API_KEY = cfg.minimax_api_key()
MINIMAX_MODEL = cfg.minimax_model()
SAMPLE_RATE = int(cfg.get("minimax_sample_rate", 32000))
TTS_VOLUME = cfg.tts_volume()
WS_URL = "wss://api.minimax.io/ws/v1/t2a_v2"
WS_OPEN_TIMEOUT = 3
WS_CLOSE_TIMEOUT = 1
TASK_STARTED_TIMEOUT = 3
FAST_FAIL_TIMEOUT = 1.5
FAST_FAIL_RESET_SECONDS = 20.0
FAST_FAIL_AFTER_FAILURES = 2

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


def _connect_ws():
    """Open MiniMax WebSocket with library keepalive disabled.

    MiniMax TTS connections can sit idle between utterances. The default
    websockets keepalive thread may time out during that idle period and print a
    noisy traceback ("keepalive ping failed"). The receiver loop below already
    detects closed connections and reconnects, so avoid protocol-level pings.
    """
    import websockets.sync.client as ws_sync

    return ws_sync.connect(
        WS_URL,
        additional_headers=_ws_headers(),
        open_timeout=WS_OPEN_TIMEOUT,
        close_timeout=WS_CLOSE_TIMEOUT,
        ping_interval=None,
    )


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
    from websockets.exceptions import ConnectionClosed

    with _connect_ws() as ws:
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
            if event == "connected_success":
                continue
            if event == "task_started":
                ws.send(json.dumps(_task_continue(text)))
            elif event == "task_continued":
                payload = data.get("data", {})
                audio = _decode_audio_chunk(payload)
                if audio is not None and len(audio) > 0:
                    yield audio
                if data.get("is_final") or payload.get("is_final"):
                    break
            elif event == "task_failed":
                logger.warning(
                    "MiniMax TTS task_failed: %s",
                    data.get("base_resp", {}).get("status_msg", "unknown error"),
                )
                break
            elif event == "task_finished":
                break
        try:
            ws.send(json.dumps({"event": "task_finish"}))
        except Exception:
            pass


def text_to_speech_stream(text: str, voice: str = None, speed: float = 1.0) -> Generator[Tuple[np.ndarray, int], None, None]:
    cc = len(text)
    if cc:
        token_usage.record(MINIMAX_MODEL, "tts", cc, 0)
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


_SENTENCE_END = re.compile(r"[。！？?!；;，,：:、…~\-]")

_SENTENCE_END = re.compile(r"[\u3002\uff01\uff1f?!\uff1b;\uff0c,\uff1a:\u3001\u2026~\-]")


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
        self._write_buffer_samples = int(SAMPLE_RATE * float(cfg.get("minimax_tts_write_buffer_seconds", 0.08)))
        self._buf: list[str] = []
        self._is_playing = False
        self._should_stop = False
        self._soft_stop = False  # Finish current sentence/phrase then stop gracefully
        self._state_lock = threading.Lock()
        # Optional callback: called with each audio chunk before playback.
        # Used by AEC to capture the far-end reference signal.
        self.on_audio_frame: "Optional[Callable[[np.ndarray], None]]" = None
        self._llm_text_started_at = 0.0
        self._llm_to_tts_logged = False
        self._audio_queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self._play_thread: threading.Thread | None = None
        self._stream: sd.OutputStream | None = None
        self._ws: object = None
        self._ws_started = threading.Event()
        self._ws_recv_thread: threading.Thread | None = None
        self._pending_count = 0
        self._pending_lock = threading.Lock()
        self._inflight_texts: deque[str] = deque()
        self._all_done = threading.Event()
        self._all_done.set()
        self._session_done = False
        self._prepare_fail_count = 0
        self._last_prepare_fail_at = 0.0

    @property
    def is_playing(self) -> bool:
        with self._state_lock:
            return self._is_playing or not self._audio_queue.empty()

    def prepare(self) -> bool:
        """Ensure TTS is ready for a new session. Returns True if WS is ready."""
        # 音频流 / 播放线程：只重建一次
        if self._stream is None:
            self._stream = sd.OutputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=2048,
            )
            self._stream.start()
        if self._play_thread is None or not self._play_thread.is_alive():
            self._play_thread = threading.Thread(target=self._play_worker, daemon=True)
            self._play_thread.start()

        # WS 还活着 → 无需重建
        if (
            self._ws_started.is_set()
            and self._ws is not None
            and self._ws_recv_thread is not None
            and self._ws_recv_thread.is_alive()
            and not self._session_done
        ):
            return True

        # WS 已死（中断后）→ 清理旧 recv 线程，重新建连
        self._reset_session_state()

        self._all_done.set()
        self._session_done = False
        self._llm_text_started_at = 0.0
        self._llm_to_tts_logged = False
        with self._pending_lock:
            self._pending_count = 0
            self._inflight_texts.clear()
        self._ws_recv_thread = threading.Thread(target=self._ws_recv_worker, daemon=True)
        self._ws_recv_thread.start()
        timeout = self._prepare_wait_timeout()
        if not self._ws_started.wait(timeout=timeout):
            self._prepare_fail_count += 1
            self._last_prepare_fail_at = time.monotonic()
            logger.warning("MiniMax TTS task_started timeout — TTS will be silent this turn")
            self._reset_session_state()
            return False
        self._prepare_fail_count = 0
        return True

    def _prepare_wait_timeout(self) -> float:
        if self._prepare_fail_count >= FAST_FAIL_AFTER_FAILURES:
            if time.monotonic() - self._last_prepare_fail_at <= FAST_FAIL_RESET_SECONDS:
                return FAST_FAIL_TIMEOUT
            self._prepare_fail_count = 0
        return TASK_STARTED_TIMEOUT

    def _reset_session_state(self) -> None:
        self._session_done = True
        self._buf = []
        with self._pending_lock:
            self._pending_count = 0
            self._inflight_texts.clear()
            self._all_done.set()
        with self._state_lock:
            self._is_playing = False
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._ws_started.clear()

    def _try_send(self, text: str) -> bool:
        """Send a sentence. Returns False if WS is down (caller should retry)."""
        if self._session_done or not self._ws_started.is_set() or not self._ws:
            return False
        cc = len(text)
        if cc:
            token_usage.record(MINIMAX_MODEL, "tts", cc, 0)
        try:
            with self._pending_lock:
                self._inflight_texts.append(text)
                self._pending_count += 1
                self._all_done.clear()
            self._ws.send(json.dumps(_task_continue(text)))
            return True
        except Exception as exc:
            logger.debug("TTS send failed: %s", exc)
            with self._pending_lock:
                self._requeue_inflight_locked()
                self._pending_count = 0
                self._all_done.set()
            self._ws_started.clear()
            self._session_done = True
            return False

    def _requeue_inflight_locked(self) -> None:
        if not self._inflight_texts:
            return
        combined = "".join(text for text in self._inflight_texts if text)
        self._inflight_texts.clear()
        if not combined:
            return
        if self._buf:
            self._buf.insert(0, combined)
        else:
            self._buf = [combined]

    def _mark_one_text_done(self) -> None:
        with self._pending_lock:
            if self._inflight_texts:
                self._inflight_texts.popleft()
            self._pending_count = max(0, self._pending_count - 1)
            if self._pending_count == 0:
                self._all_done.set()

    def push(self, text: str) -> None:
        """Accumulate text; send complete sentences when WS is available."""
        if self._prepare_fail_count >= FAST_FAIL_AFTER_FAILURES and (
            time.monotonic() - self._last_prepare_fail_at <= FAST_FAIL_RESET_SECONDS
        ):
            return
        if text and not self._llm_text_started_at:
            self._llm_text_started_at = time.perf_counter()
        self._buf.append(text)
        while True:
            combined = "".join(self._buf)
            if not combined.strip():
                self._buf = []
                break
            match = _SENTENCE_END.search(combined)
            send_text = ""
            rest = ""
            if match:
                idx = match.end()
                sentence = combined[:idx]
                stripped = sentence.strip()
                if stripped:
                    send_text = stripped
                    rest = combined[idx:]

            if not send_text:
                break
            if self._session_done or not self._ws_started.is_set():
                if not self.prepare():
                    break
            if self._try_send(send_text):
                self._buf = [rest] if rest else []
            else:
                break  # WS down, retry on next push() or end_sentence()

    def end_sentence(self, wait: bool = True) -> None:
        """Flush remaining text, then wait for audio to complete.

        Keeps the WebSocket alive for reuse on the next utterance,
        avoiding a 4-5s cold-start reconnect."""
        if self._prepare_fail_count >= FAST_FAIL_AFTER_FAILURES and (
            time.monotonic() - self._last_prepare_fail_at <= FAST_FAIL_RESET_SECONDS
        ):
            self._buf = []
            return
        if not self._buf and self._pending_count == 0 and not self._ws_started.is_set():
            return
        deadline = time.perf_counter() + 30
        while True:
            remaining = "".join(self._buf).strip()
            if not remaining:
                break
            if self._session_done or not self._ws_started.is_set():
                if not self.prepare():
                    if time.perf_counter() > deadline or self._should_stop:
                        self._buf = []
                        break
                    time.sleep(0.1)
                    continue
            if self._try_send(remaining):
                self._buf = []
                break
            if time.perf_counter() > deadline or self._should_stop:
                self._buf = []
                break
            time.sleep(0.1)

        if wait:
            # Wait for all pending sentences to finish
            self._all_done.wait(timeout=30)

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

    def soft_interrupt(self) -> None:
        """Graceful stop — finish the current audio chunk then yield the floor.

        Unlike hard_interrupt, this does NOT drain the pending queue or close
        the WebSocket.  The play worker will stop after writing the current
        chunk, keeping the connection alive for the next utterance.
        """
        self._soft_stop = True
        # Allow the recv worker to reconnect if the server closes the connection
        self._session_done = False

    def interrupt(self) -> None:
        """Stop playback immediately and reset state for a new session."""
        self._should_stop = True
        self._soft_stop = False
        self._session_done = True
        self._buf = []
        with self._pending_lock:
            self._pending_count = 0
            self._inflight_texts.clear()
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
        # 不销毁 _stream — 播放线程和音频输出保持存活，避免 is_playing 永久卡死
        with self._state_lock:
            self._is_playing = False
        self._should_stop = False
        self._session_done = False  # recv 线程自动重连 WS，供下一轮 TTS 使用
        self._llm_text_started_at = 0.0
        self._llm_to_tts_logged = False

    def _ws_recv_worker(self) -> None:
        """Receiver loop. Reconnects automatically on unexpected drops."""
        from websockets.exceptions import ConnectionClosed

        while not self._should_stop:
            self._ws_started.clear()
            try:
                self._ws = _connect_ws()
                self._ws.send(json.dumps(_task_start(self._voice_id, self._speed)))
            except Exception:
                if self._should_stop:
                    return
                time.sleep(0.5)
                continue

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

                    if event == "connected_success":
                        continue
                    if event == "task_started":
                        self._ws_started.set()
                    elif event == "task_continued":
                        audio = _decode_audio_chunk(data.get("data", {}))
                        if audio is not None and len(audio) > 0:
                            self._audio_queue.put(audio)
                        if data.get("is_final") or data.get("data", {}).get("is_final"):
                            self._mark_one_text_done()
                    elif event == "task_finished":
                        self._audio_queue.put(None)
                        self._ws_started.clear()
                        with self._pending_lock:
                            self._pending_count = 0
                            self._inflight_texts.clear()
                            self._all_done.set()
                        # Keep connection alive: start next task immediately
                        try:
                            self._ws.send(json.dumps(_task_start(self._voice_id, self._speed)))
                        except Exception:
                            break
                        continue
                    elif event == "task_failed":
                        status_msg = str(data.get("base_resp", {}).get("status_msg", "unknown error") or "")
                        logger.warning(
                            "MiniMax TTS task_failed: %s",
                            status_msg,
                        )
                        self._audio_queue.put(None)
                        self._ws_started.clear()
                        with self._pending_lock:
                            self._requeue_inflight_locked()
                            self._pending_count = 0
                            self._all_done.set()
                        if "no messages received" in status_msg.lower():
                            self._session_done = True
                        break
            except Exception:
                pass
            finally:
                self._ws_started.clear()
                # Reset pending on unexpected disconnect — lost sentence audio
                # will be compensated by re-sending from buffer on the new connection.
                with self._pending_lock:
                    self._requeue_inflight_locked()
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
        write_buf: list[np.ndarray] = []
        write_buf_samples = 0

        def write_audio(audio: np.ndarray) -> bool:
            nonlocal write_buf, write_buf_samples
            if len(audio) == 0:
                return True
            write_buf.append(audio)
            write_buf_samples += len(audio)
            if write_buf_samples < self._write_buffer_samples:
                return True
            return flush_write_buf()

        def flush_write_buf() -> bool:
            nonlocal write_buf, write_buf_samples
            if not write_buf:
                return True
            chunk = np.concatenate(write_buf) if len(write_buf) > 1 else write_buf[0]
            write_buf = []
            write_buf_samples = 0
            chunk = _apply_volume(chunk)
            if self.on_audio_frame:
                self.on_audio_frame(chunk)
            try:
                stream.write(chunk)
                return True
            except Exception:
                self._should_stop = True
                return False

        try:
            prebuf: list[np.ndarray] = []
            prebuf_samples = 0
            started = False
            while not self._should_stop:
                # Check for graceful stop request
                if self._soft_stop:
                    self._soft_stop = False
                    prebuf = []
                    prebuf_samples = 0
                    write_buf = []
                    write_buf_samples = 0
                    started = False
                    with self._state_lock:
                        self._is_playing = False
                    # Don't drain the audio queue — keep connection alive

                try:
                    audio = self._audio_queue.get(timeout=0.15)
                except queue.Empty:
                    if started:
                        if not flush_write_buf():
                            return
                        with self._state_lock:
                            self._is_playing = False
                    continue

                if audio is None:
                    if prebuf:
                        for chunk in prebuf:
                            if self._soft_stop:
                                self._soft_stop = False
                                break
                            if not write_audio(chunk):
                                return
                        if not flush_write_buf():
                            return
                        prebuf = []
                    elif not flush_write_buf():
                        return
                    started = False
                    prebuf_samples = 0
                    with self._state_lock:
                        self._is_playing = False
                    continue

                if not started:
                    if self._soft_stop:
                        self._soft_stop = False
                        continue
                    prebuf.append(audio)
                    prebuf_samples += len(audio)
                    if prebuf_samples >= self._buffer_samples:
                        if not self._llm_to_tts_logged and self._llm_text_started_at:
                            self._llm_to_tts_logged = True
                            print(f"\n  [latency] llm_to_tts {time.perf_counter() - self._llm_text_started_at:.2f}s")
                        with self._state_lock:
                            self._is_playing = True
                        started = True
                        for chunk in prebuf:
                            if self._soft_stop:
                                self._soft_stop = False
                                started = False
                                break
                            if not write_audio(chunk):
                                return
                        prebuf = []
                    continue

                # -- started is True, play individual chunks --
                with self._state_lock:
                    self._is_playing = True
                if not write_audio(audio):
                    return
                if self._soft_stop:
                    self._soft_stop = False
                    if not flush_write_buf():
                        return
                    with self._state_lock:
                        self._is_playing = False
                    started = False
                    prebuf = []
                    prebuf_samples = 0
        finally:
            flush_write_buf()
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
