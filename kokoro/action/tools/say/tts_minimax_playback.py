"""MiniMax streaming audio playback worker."""

from __future__ import annotations

import queue
import time

import numpy as np

from kokoro.action.tools.say.tts_minimax_oneshot import _apply_volume as apply_volume


def run_play_worker(engine) -> None:
    stream = engine._stream
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
        if write_buf_samples < engine._write_buffer_samples:
            return True
        return flush_write_buf()

    def flush_write_buf() -> bool:
        nonlocal write_buf, write_buf_samples
        if not write_buf:
            return True
        chunk = np.concatenate(write_buf) if len(write_buf) > 1 else write_buf[0]
        write_buf = []
        write_buf_samples = 0
        chunk = apply_volume(chunk)
        if engine.on_audio_frame:
            engine.on_audio_frame(chunk)
        try:
            stream.write(chunk)
            return True
        except Exception:
            engine._should_stop = True
            return False

    try:
        prebuf: list[np.ndarray] = []
        prebuf_samples = 0
        started = False
        while not engine._should_stop:
            if engine._soft_stop:
                engine._soft_stop = False
                prebuf = []
                prebuf_samples = 0
                write_buf = []
                write_buf_samples = 0
                started = False
                with engine._state_lock:
                    engine._is_playing = False

            try:
                audio = engine._audio_queue.get(timeout=0.15)
            except queue.Empty:
                if started:
                    if not flush_write_buf():
                        return
                    with engine._state_lock:
                        engine._is_playing = False
                continue

            if audio is None:
                if prebuf:
                    for chunk in prebuf:
                        if engine._soft_stop:
                            engine._soft_stop = False
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
                with engine._state_lock:
                    engine._is_playing = False
                continue

            if not started:
                if engine._soft_stop:
                    engine._soft_stop = False
                    continue
                prebuf.append(audio)
                prebuf_samples += len(audio)
                if prebuf_samples >= engine._buffer_samples:
                    if not engine._llm_to_tts_logged and engine._llm_text_started_at:
                        engine._llm_to_tts_logged = True
                        print(f"\n  [latency] llm_to_tts {time.perf_counter() - engine._llm_text_started_at:.2f}s")
                    with engine._state_lock:
                        engine._is_playing = True
                    started = True
                    for chunk in prebuf:
                        if engine._soft_stop:
                            engine._soft_stop = False
                            started = False
                            break
                        if not write_audio(chunk):
                            return
                    prebuf = []
                continue

            with engine._state_lock:
                engine._is_playing = True
            if not write_audio(audio):
                return
            if engine._soft_stop:
                engine._soft_stop = False
                if not flush_write_buf():
                    return
                with engine._state_lock:
                    engine._is_playing = False
                started = False
                prebuf = []
                prebuf_samples = 0
    finally:
        flush_write_buf()
        with engine._state_lock:
            engine._is_playing = False
