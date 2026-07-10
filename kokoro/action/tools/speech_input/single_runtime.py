"""Single-character speech input runtime."""

from __future__ import annotations

import threading
from collections.abc import Callable

from kokoro.action.tools.speech_input.buffer import PendingSpeechTurnBuffer
from kokoro.action.tools.speech_input.startup import create_default_conversation
from kokoro.action.tools.speech_input.turns import (
    flush_single_speech_turn,
    handle_single_direct_speech_turn,
    handle_stt_pool_turn,
    queue_single_user_utterance,
)
from kokoro.action.tools.speech_input.worker import create_single_partial_handler, start_default_worker


class SingleSpeechRuntime:
    """Owns single-character STT turn buffering and speech callbacks."""

    def __init__(
        self,
        *,
        is_probable_tts_echo: Callable[[str], bool],
        machine,
        session,
        dialogue,
        action_runtime,
        cancel_slot: list[threading.Event | None],
        transports,
        tool_enabled: bool,
        stt_refine_inline: bool,
        dialogue_pool_enabled: bool,
        tts_engine=None,
        aec_processor=None,
        subtitle_client=None,
        stt_subtitle_client=None,
        display_user: Callable[[str], None] = print,
        printer: Callable[[str], None] = print,
    ) -> None:
        self.pending_turn = PendingSpeechTurnBuffer(default_reason="endpoint")
        self.pool_lock = threading.Lock()
        self.conversation = None
        self.is_probable_tts_echo = is_probable_tts_echo
        self.machine = machine
        self.session = session
        self.dialogue = dialogue
        self.action_runtime = action_runtime
        self.cancel_slot = cancel_slot
        self.transports = transports
        self.tool_enabled = tool_enabled
        self.stt_refine_inline = stt_refine_inline
        self.dialogue_pool_enabled = dialogue_pool_enabled
        self.tts_engine = tts_engine
        self.aec_processor = aec_processor
        self.subtitle_client = subtitle_client
        self.stt_subtitle_client = stt_subtitle_client
        self.display_user = display_user
        self.printer = printer

    def maybe_flush_user_turn(self, force: bool = False) -> None:
        flush_single_speech_turn(
            pending_turn=self.pending_turn,
            force=force,
            is_probable_tts_echo=self.is_probable_tts_echo,
            conversation=self.conversation,
            machine=self.machine,
            session=self.session,
            dialogue=self.dialogue,
            stt_subtitle_client=self.stt_subtitle_client,
            dialogue_pool_enabled=self.dialogue_pool_enabled,
            handle_conversation=self.handle_conversation,
            handle_stt_pool_turn=self.handle_stt_pool_turn,
            display_user=self.display_user,
            printer=self.printer,
        )

    def on_user_utterance(self, text: str) -> None:
        queue_single_user_utterance(
            text=text,
            pending_turn=self.pending_turn,
            is_probable_tts_echo=self.is_probable_tts_echo,
            conversation=self.conversation,
            machine=self.machine,
            cancel_slot=self.cancel_slot,
            tts_engine=self.tts_engine,
            aec_processor=self.aec_processor,
            stt_subtitle_client=self.stt_subtitle_client,
            printer=self.printer,
        )

    def handle_stt_pool_turn(self, pool_text: str) -> None:
        if self._life_runtime_primary():
            from kokoro.action import input_sources

            text = str(pool_text or "").strip()
            if text:
                self.display_user(text)
                self.dialogue.cancel_plans()
                input_sources.publish_speech_text(
                    self.session,
                    text,
                    speaker=self.session.user_name,
                    reason="stt_pool",
                )
            self._release_input_turn_to_life_runtime()
            return
        handle_stt_pool_turn(
            pool_text=pool_text,
            pool_lock=self.pool_lock,
            pending_turn=self.pending_turn,
            cancel_slot=self.cancel_slot,
            machine=self.machine,
            dialogue=self.dialogue,
            action_runtime=self.action_runtime,
            session=self.session,
            stt_subtitle_client=self.stt_subtitle_client,
            display_user=self.display_user,
            printer=self.printer,
        )

    def handle_conversation(self, text: str) -> None:
        if self._life_runtime_primary():
            self.printer(f"\n  [trace] life_runtime_input_published text={len(str(text or '').strip())}ch")
            self._release_input_turn_to_life_runtime()
            return
        handle_single_direct_speech_turn(
            text=text,
            cancel_slot=self.cancel_slot,
            machine=self.machine,
            dialogue=self.dialogue,
            action_runtime=self.action_runtime,
            session=self.session,
            transports=self.transports,
            tool_enabled=self.tool_enabled,
            stt_refine_inline=self.stt_refine_inline,
            subtitle_client=self.subtitle_client,
            tts_engine=self.tts_engine,
            trace_printer=self.printer,
        )

    def create_conversation(self, *, recognizer):
        self.conversation = create_default_conversation(
            recognizer=recognizer,
            machine=self.machine,
            on_user_utterance=self.on_user_utterance,
            on_partial=create_single_partial_handler(
                is_probable_tts_echo=self.is_probable_tts_echo,
                machine=self.machine,
                stt_subtitle_client=self.stt_subtitle_client,
            ),
        )
        return self.conversation

    def _life_runtime_primary(self) -> bool:
        life_runtime = getattr(self.session, "life_runtime", None)
        if life_runtime is None or not bool(getattr(life_runtime, "enabled", False)):
            return False
        section = getattr(life_runtime, "section", {})
        if isinstance(section, dict):
            return bool(section.get("primary", True))
        return True

    def _release_input_turn_to_life_runtime(self) -> None:
        from kokoro.core import state_machine as sm

        self.cancel_slot[0] = None
        if getattr(self.machine, "state", None) == sm.SystemState.THINKING:
            self.machine.emit(sm.SystemEvent.LLM_DONE)
            self.machine.set_tts_state(sm.TTSState.IDLE)
            self.machine.emit(sm.SystemEvent.TTS_DONE)
        self.machine.reset_error_count()
        self.machine.set_stt_state(sm.STTState.LISTENING)

    def start_worker(self, *, device: int | None) -> threading.Thread:
        return start_default_worker(
            machine=self.machine,
            device=device,
            conversation=self.conversation,
            aec_processor=self.aec_processor,
            tts_engine=self.tts_engine,
            flush_callback=self.maybe_flush_user_turn,
            error_source="stt",
        )


def create_single_speech_runtime(**kwargs) -> SingleSpeechRuntime:
    return SingleSpeechRuntime(**kwargs)


def create_single_speech_runtime_from_outputs(
    *,
    output_resources,
    machine,
    session,
    dialogue,
    action_runtime,
    cancel_slot: list[threading.Event | None],
    transports,
    tool_enabled: bool,
    stt_refine_inline: bool,
    dialogue_pool_enabled: bool,
    display_user: Callable[[str], None] = print,
    printer: Callable[[str], None] = print,
) -> SingleSpeechRuntime:
    return create_single_speech_runtime(
        is_probable_tts_echo=output_resources.is_probable_tts_echo,
        machine=machine,
        session=session,
        dialogue=dialogue,
        action_runtime=action_runtime,
        cancel_slot=cancel_slot,
        transports=transports,
        tool_enabled=tool_enabled,
        stt_refine_inline=stt_refine_inline,
        dialogue_pool_enabled=dialogue_pool_enabled,
        tts_engine=output_resources.tts_engine,
        aec_processor=output_resources.aec_processor,
        subtitle_client=output_resources.subtitle_client,
        stt_subtitle_client=output_resources.stt_subtitle_client,
        display_user=display_user,
        printer=printer,
    )
