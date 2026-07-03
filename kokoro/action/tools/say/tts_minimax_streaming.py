"""Persistent streaming MiniMax TTS engine."""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections import deque
from typing import Callable, Optional

import sounddevice as sd

from kokoro.core import config as cfg
from kokoro.core import token_usage
from kokoro.action.tools.say.tts_minimax_config import (
    FAST_FAIL_AFTER_FAILURES,
    FAST_FAIL_RESET_SECONDS,
    FAST_FAIL_TIMEOUT,
    MINIMAX_MODEL,
    SAMPLE_RATE,
    TASK_STARTED_TIMEOUT,
    resolve_voice,
)
from kokoro.action.tools.say.tts_minimax_playback import run_play_worker
from kokoro.action.tools.say.tts_minimax_protocol import task_continue
from kokoro.action.tools.say.tts_minimax_ws import run_ws_recv_worker

logger = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"[\u3002\uff01\uff1f?!\uff1b;\uff0c,\uff1a:\u3001\u2026~\-]")


class StreamingTTS:
    """Streaming TTS with persistent WebSocket and automatic reconnect.

    push() accumulates text. When a sentence-ending punctuation is detected,
    the complete sentence is sent via task_continue.  If the WebSocket drops,
    the sentence stays in the buffer and the receiver reconnects transparently.
    """

    def __init__(self, voice: str = None):
        self._voice_id = resolve_voice(voice)
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
            self._ws.send(json.dumps(task_continue(text)))
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
        run_ws_recv_worker(self)

    def _play_worker(self) -> None:
        run_play_worker(self)
