"""Multi-character speech turn queue and flush helpers."""

from __future__ import annotations

from collections.abc import Callable

from kokoro.action.tools.speech_input.buffer import PendingSpeechTurnBuffer


def flush_multi_speech_turn(
    *,
    pending_turn: PendingSpeechTurnBuffer,
    force: bool,
    is_probable_tts_echo: Callable[[str], bool],
    conversation,
    machine,
    speech_gate,
    aec_processor=None,
    handle_user_text: Callable[..., None],
    prefetch: bool,
    printer: Callable[[str], None] = print,
) -> None:
    from kokoro.core import state_machine as sm

    turn = pending_turn.pop_ready(force=force)
    if turn is None:
        return
    if is_probable_tts_echo(turn.text):
        conversation.reset_stream()
        printer("\n  [stt] dropped probable tts echo at flush")
        machine.set_stt_state(sm.STTState.LISTENING)
        return
    machine.emit(sm.SystemEvent.STT_REFINED)
    speech_gate.hold(2.0)
    if aec_processor is not None:
        aec_processor.reset()
    handle_user_text(turn.text, prefetch=prefetch)
    machine.set_stt_state(sm.STTState.LISTENING)


def queue_multi_user_utterance(
    *,
    text: str,
    pending_turn: PendingSpeechTurnBuffer,
    is_probable_tts_echo: Callable[[str], bool],
    conversation,
    machine,
    printer: Callable[[str], None] = print,
) -> None:
    from kokoro.core import state_machine as sm

    if is_probable_tts_echo(text):
        conversation.reset_stream()
        printer("\n  [stt] dropped probable tts echo")
        machine.set_stt_state(sm.STTState.LISTENING)
        return
    pending_turn.queue(text)
