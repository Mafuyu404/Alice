"""MiniMax WebSocket protocol helpers."""

from __future__ import annotations

import json
import logging
from typing import Generator, Optional

import numpy as np

from kokoro.action.tools.say.tts_minimax_config import (
    MINIMAX_API_KEY,
    MINIMAX_MODEL,
    SAMPLE_RATE,
    WS_CLOSE_TIMEOUT,
    WS_OPEN_TIMEOUT,
    WS_URL,
)

logger = logging.getLogger(__name__)


def ws_headers() -> dict:
    return {"Authorization": f"Bearer {MINIMAX_API_KEY}"}


def connect_ws():
    """Open MiniMax WebSocket with library keepalive disabled.

    MiniMax TTS connections can sit idle between utterances. The default
    websockets keepalive thread may time out during that idle period and print a
    noisy traceback ("keepalive ping failed"). The receiver loop below already
    detects closed connections and reconnects, so avoid protocol-level pings.
    """
    import websockets.sync.client as ws_sync

    return ws_sync.connect(
        WS_URL,
        additional_headers=ws_headers(),
        open_timeout=WS_OPEN_TIMEOUT,
        close_timeout=WS_CLOSE_TIMEOUT,
        ping_interval=None,
    )


def task_start(voice_id: str, speed: float) -> dict:
    return {
        "event": "task_start",
        "model": MINIMAX_MODEL,
        "voice_setting": {"voice_id": voice_id, "speed": speed, "vol": 1.0, "pitch": 0},
        "audio_setting": {"sample_rate": SAMPLE_RATE, "format": "pcm", "channel": 1},
    }


def task_continue(text: str) -> dict:
    return {"event": "task_continue", "text": text}


def decode_audio_chunk(data: dict) -> Optional[np.ndarray]:
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


def send_and_receive_stream(text: str, voice_id: str, speed: float) -> Generator[np.ndarray, None, None]:
    from websockets.exceptions import ConnectionClosed

    with connect_ws() as ws:
        ws.send(json.dumps(task_start(voice_id, speed)))
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
                ws.send(json.dumps(task_continue(text)))
            elif event == "task_continued":
                payload = data.get("data", {})
                audio = decode_audio_chunk(payload)
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
