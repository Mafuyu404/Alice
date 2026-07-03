"""MiniMax streaming WebSocket receive worker."""

from __future__ import annotations

import json
import logging
import time

from kokoro.action.tools.say.tts_minimax_protocol import connect_ws, decode_audio_chunk, task_start

logger = logging.getLogger(__name__)


def run_ws_recv_worker(engine) -> None:
    """Receiver loop. Reconnects automatically on unexpected drops."""
    from websockets.exceptions import ConnectionClosed

    while not engine._should_stop:
        engine._ws_started.clear()
        try:
            engine._ws = connect_ws()
            engine._ws.send(json.dumps(task_start(engine._voice_id, engine._speed)))
        except Exception:
            if engine._should_stop:
                return
            time.sleep(0.5)
            continue

        try:
            while not engine._should_stop:
                try:
                    msg = engine._ws.recv(timeout=1)
                except TimeoutError:
                    if engine._session_done:
                        return
                    continue
                except ConnectionClosed:
                    break

                if isinstance(msg, bytes):
                    continue
                data = json.loads(msg)
                event = data.get("event", "")

                if event == "connected_success":
                    continue
                if event == "task_started":
                    engine._ws_started.set()
                elif event == "task_continued":
                    audio = decode_audio_chunk(data.get("data", {}))
                    if audio is not None and len(audio) > 0:
                        engine._audio_queue.put(audio)
                    if data.get("is_final") or data.get("data", {}).get("is_final"):
                        engine._mark_one_text_done()
                elif event == "task_finished":
                    engine._audio_queue.put(None)
                    engine._ws_started.clear()
                    with engine._pending_lock:
                        engine._pending_count = 0
                        engine._inflight_texts.clear()
                        engine._all_done.set()
                    try:
                        engine._ws.send(json.dumps(task_start(engine._voice_id, engine._speed)))
                    except Exception:
                        break
                    continue
                elif event == "task_failed":
                    status_msg = str(data.get("base_resp", {}).get("status_msg", "unknown error") or "")
                    logger.warning("MiniMax TTS task_failed: %s", status_msg)
                    engine._audio_queue.put(None)
                    engine._ws_started.clear()
                    with engine._pending_lock:
                        engine._requeue_inflight_locked()
                        engine._pending_count = 0
                        engine._all_done.set()
                    if "no messages received" in status_msg.lower():
                        engine._session_done = True
                    break
        except Exception:
            pass
        finally:
            engine._ws_started.clear()
            with engine._pending_lock:
                engine._requeue_inflight_locked()
                engine._pending_count = 0
                engine._all_done.set()
            if engine._ws:
                try:
                    engine._ws.close()
                except Exception:
                    pass
                engine._ws = None

        if engine._should_stop or engine._session_done:
            return
        time.sleep(0.2)
