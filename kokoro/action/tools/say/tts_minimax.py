"""MiniMax TTS backend."""

from __future__ import annotations

from typing import Optional

from kokoro.action.tools.say.tts_minimax_config import (
    MINIMAX_API_KEY,
    MINIMAX_MODEL,
    SAMPLE_RATE,
    TTS_VOLUME,
    VOICE_PRESETS,
    WS_URL,
    FAST_FAIL_AFTER_FAILURES,
    FAST_FAIL_RESET_SECONDS,
    FAST_FAIL_TIMEOUT,
    TASK_STARTED_TIMEOUT,
    resolve_voice as _resolve_voice,
)
from kokoro.action.tools.say.tts_minimax_oneshot import (
    _apply_volume,
    _play_audio_chunks,
    enqueue_tts,
    play_tts,
    stop_playback,
    text_to_speech,
    text_to_speech_stream,
)
from kokoro.action.tools.say.tts_minimax_protocol import (
    connect_ws as _connect_ws,
    decode_audio_chunk as _decode_audio_chunk,
    send_and_receive_stream as _send_and_receive_stream,
    task_continue as _task_continue,
    task_start as _task_start,
    ws_headers as _ws_headers,
)
from kokoro.action.tools.say.tts_minimax_streaming import StreamingTTS

_warmed_up = False
_streaming_tts: Optional[StreamingTTS] = None


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
