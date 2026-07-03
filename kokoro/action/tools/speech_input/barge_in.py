"""Barge-in handling for speech input."""

from __future__ import annotations

import threading


def handle_barge_in(
    *,
    machine,
    reason: str,
    cancel_slot: list[threading.Event | None],
    tts_engine=None,
    aec_processor=None,
) -> bool:
    if not getattr(machine, "is_busy", False):
        return False
    cancel = cancel_slot[0] if cancel_slot else None
    if cancel:
        cancel.set()
    from kokoro.core import state_machine as sm

    is_overlap = getattr(machine, "tts_state", None) in (sm.TTSState.STREAMING, sm.TTSState.DRAINING)
    if tts_engine:
        if is_overlap and "hard_break" in str(reason or ""):
            tts_engine.interrupt()
        elif is_overlap and hasattr(tts_engine, "soft_interrupt"):
            tts_engine.soft_interrupt()
        else:
            tts_engine.interrupt()
    if aec_processor is not None:
        aec_processor.reset()
    machine.emit(sm.SystemEvent.USER_SPEECH_START)
    machine.set_stt_state(sm.STTState.LISTENING)
    return True
