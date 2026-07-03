"""Dialogue-pool speech turn handling."""

from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Callable

from kokoro.action import dialogue_orchestrator as dialogue_mod
from kokoro.action import input_sources
from kokoro.action.tools.speech_input.buffer import PendingSpeechTurnBuffer


def handle_stt_pool_turn(
    *,
    pool_text: str,
    pool_lock: threading.Lock,
    pending_turn: PendingSpeechTurnBuffer,
    cancel_slot: list[threading.Event | None],
    machine,
    dialogue,
    action_runtime,
    session,
    stt_subtitle_client=None,
    display_user: Callable[[str], None] = print,
    printer: Callable[[str], None] = print,
) -> None:
    from kokoro.core import state_machine as sm

    t_dispatch = time.perf_counter()
    if not pool_lock.acquire(blocking=False):
        pending_turn.prepend(pool_text)
        return

    cancel_event = threading.Event()
    cancel_slot[0] = cancel_event
    try:
        if not machine.emit(sm.SystemEvent.STT_REFINED):
            pending_turn.prepend(pool_text)
            return

        decision = dialogue.decide_stt_pool_turn(pool_text=pool_text)
        if cancel_event.is_set():
            return

        if decision.action == "wait":
            pending_turn.extend_or_replace(pool_text, min_delay=2.5)
            machine.emit(sm.SystemEvent.LLM_DONE)
            machine.set_tts_state(sm.TTSState.IDLE)
            machine.emit(sm.SystemEvent.TTS_DONE)
            machine.reset_error_count()
            machine.set_stt_state(sm.STTState.LISTENING)
            return

        consumed = decision.consumed_text.strip() or pool_text.strip()
        remaining = decision.remaining_text.strip()
        if remaining:
            pending_turn.replace_if_empty(remaining)

        if stt_subtitle_client:
            stt_subtitle_client.clear()
        display_user(consumed)
        dialogue.cancel_plans()
        input_sources.publish_speech_text(
            session,
            consumed,
            speaker=session.user_name,
            reason="stt_pool",
        )
        machine.emit(sm.SystemEvent.LLM_DONE)

        reply = dialogue_mod.clean_generated_reply(decision.reply, session.character_name)
        t_reply_ready = time.perf_counter()
        printer(f"  [trace] llm {t_reply_ready - t_dispatch:.1f}s  text={len(reply or '')}ch")
        batch = dialogue.precomputed_say_batch(
            user_text=consumed,
            reply=reply,
            reason=decision.intent or "stt pool reply",
            cancel_event=cancel_event,
        )
        action_runtime.execute_batch(batch)
        t_play_done = time.perf_counter()
        printer(f"  [trace] action_say {t_play_done - t_reply_ready:.1f}s  total {t_play_done - t_dispatch:.1f}s")
        if cancel_event.is_set():
            return
        machine.set_stt_state(sm.STTState.LISTENING)
    except Exception as exc:
        printer(f"\n[error] handle_stt_pool_turn: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        machine.emit_error("handle_stt_pool_turn")
    finally:
        cancel_slot[0] = None
        pool_lock.release()
