"""Single-character speech turn queue and direct handling helpers."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable

from kokoro.action import dialogue_orchestrator as dialogue_mod
from kokoro.action import input_sources
from kokoro.action.tools.speech_input.barge_in import handle_barge_in
from kokoro.action.tools.speech_input.buffer import PendingSpeechTurnBuffer


def handle_direct_speech_turn(
    *,
    text: str,
    cancel_slot: list[threading.Event | None],
    machine,
    dialogue,
    action_runtime,
    session,
    make_default_decision: Callable[[], object],
    screen_command_handler: Callable[[str, threading.Event], str],
    context_augmenter: Callable[[str], str],
    boundary_reply_for_text: Callable[[str], str],
    stt_refine_inline: bool,
    subtitle_client=None,
    trace_printer: Callable[[str], None] = print,
) -> None:
    trace_printer(f"\n  [trace] handler_start text={len(text)}ch")
    cancel_event = threading.Event()
    cancel_slot[0] = cancel_event

    try:
        reply = boundary_reply_for_text(text)
        if reply:
            batch = dialogue.precomputed_say_batch(
                user_text=text,
                reply=reply,
                reason="local qq runtime boundary",
                cancel_event=cancel_event,
            )
            action_runtime.execute_batch(batch)
            return

        command_context = screen_command_handler(text, cancel_event)
        if cancel_event.is_set():
            cancel_slot[0] = None
            return

        command_context = context_augmenter(command_context)
        decision = make_default_decision()
        max_history_messages = 30 if ("总结" in text or "summary" in text.lower()) else None
        batch = dialogue.direct_say_batch(
            user_text=text,
            decision=decision,
            extra_context=command_context or None,
            max_history_messages=max_history_messages,
            cancel_event=cancel_event,
            stt_refine_inline=stt_refine_inline,
            usage_label="chat",
        )
        action_runtime.execute_batch(batch)
        if cancel_event.is_set():
            cancel_slot[0] = None
            return
        if subtitle_client:
            subtitle_client.clear()

    except Exception as exc:
        trace_printer(f"\n[error] handle_direct_speech_turn: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        machine.emit_error("handle_direct_speech_turn")
    finally:
        cancel_slot[0] = None


def handle_single_direct_speech_turn(
    *,
    text: str,
    cancel_slot: list[threading.Event | None],
    machine,
    dialogue,
    action_runtime,
    session,
    transports,
    tool_enabled: bool,
    stt_refine_inline: bool,
    subtitle_client=None,
    tts_engine=None,
    trace_printer: Callable[[str], None] = print,
) -> None:
    from kokoro.action.tools import observe_screen

    def make_default_decision() -> dialogue_mod.DialogueDecision:
        return dialogue_mod.DialogueDecision(
            action="speak",
            intent="回应",
            utterance_mode="normal",
            context_use="none",
        )

    def handle_screen_command(value: str, cancel_event: threading.Event) -> str:
        return observe_screen.handle_screen_command(
            value,
            session=session,
            tts_engine=tts_engine,
            cancel_event=cancel_event,
            timeout=transports.screen_vision_timeout,
            tool_enabled=tool_enabled,
        )

    handle_direct_speech_turn(
        text=text,
        cancel_slot=cancel_slot,
        machine=machine,
        dialogue=dialogue,
        action_runtime=action_runtime,
        session=session,
        make_default_decision=make_default_decision,
        screen_command_handler=handle_screen_command,
        context_augmenter=transports.augment_live_context,
        boundary_reply_for_text=transports.boundary_reply_for_text,
        stt_refine_inline=stt_refine_inline,
        subtitle_client=subtitle_client,
        trace_printer=trace_printer,
    )


def flush_single_speech_turn(
    *,
    pending_turn: PendingSpeechTurnBuffer,
    force: bool,
    is_probable_tts_echo: Callable[[str], bool],
    conversation,
    machine,
    session,
    dialogue,
    stt_subtitle_client=None,
    dialogue_pool_enabled: bool,
    handle_conversation: Callable[[str], None],
    handle_stt_pool_turn: Callable[[str], None],
    display_user: Callable[[str], None],
    printer: Callable[[str], None] = print,
) -> None:
    from kokoro.core import state_machine as sm

    turn = pending_turn.pop_ready(force=force)
    if turn is None:
        return
    if is_probable_tts_echo(turn.text):
        if stt_subtitle_client:
            stt_subtitle_client.clear()
        printer("\n  [stt] dropped probable tts echo at flush")
        conversation.reset_stream()
        machine.set_stt_state(sm.STTState.LISTENING)
        return
    printer(
        f"\n  [trace] cli_flush force={force} reason={turn.reason} text={len(turn.text)}ch "
        f"merge_wait={turn.merge_wait_seconds:.2f}s"
    )
    if not dialogue_pool_enabled:
        if stt_subtitle_client:
            stt_subtitle_client.clear()
        display_user(turn.text)
        dialogue.cancel_plans()
        input_sources.publish_speech_text(
            session,
            turn.text,
            speaker=session.user_name,
            reason=turn.reason,
        )
        if not machine.emit(sm.SystemEvent.STT_REFINED):
            return
        conversation.reset_stream()
        threading.Thread(target=handle_conversation, args=(turn.text,), daemon=True).start()
        return
    threading.Thread(target=handle_stt_pool_turn, args=(turn.text,), daemon=True).start()


def queue_single_user_utterance(
    *,
    text: str,
    pending_turn: PendingSpeechTurnBuffer,
    is_probable_tts_echo: Callable[[str], bool],
    conversation,
    machine,
    cancel_slot: list[threading.Event | None],
    tts_engine=None,
    aec_processor=None,
    stt_subtitle_client=None,
    printer: Callable[[str], None] = print,
) -> None:
    from kokoro.core import state_machine as sm

    if is_probable_tts_echo(text):
        if stt_subtitle_client:
            stt_subtitle_client.clear()
        printer("\n  [stt] dropped probable tts echo")
        conversation.reset_stream()
        machine.set_stt_state(sm.STTState.LISTENING)
        return
    reason = getattr(conversation, "last_reason", "endpoint")
    handle_barge_in(
        machine=machine,
        reason=reason,
        cancel_slot=cancel_slot,
        tts_engine=tts_engine,
        aec_processor=aec_processor,
    )
    queued_text, delay = pending_turn.queue(text, reason=reason)
    printer(
        f"\n  [trace] cli_queue reason={reason} text={len(queued_text)}ch "
        f"merge_delay={delay:.2f}s"
    )
