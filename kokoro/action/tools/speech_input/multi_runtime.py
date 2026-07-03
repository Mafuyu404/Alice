"""Multi-character speech input runtime."""

from __future__ import annotations

import threading
from collections.abc import Callable

from kokoro.action.tools.speech_input.buffer import PendingSpeechTurnBuffer
from kokoro.action.tools.speech_input.startup import create_default_conversation
from kokoro.action.tools.speech_input.turns import flush_multi_speech_turn, queue_multi_user_utterance
from kokoro.action.tools.speech_input.worker import create_partial_handler, start_default_worker


class MultiSpeechInputRuntime:
    """Owns multi-character STT turn buffering and speech callbacks."""

    def __init__(
        self,
        *,
        is_probable_tts_echo: Callable[[str], bool],
        machine,
        speech_gate,
        handle_user_text: Callable[..., None],
        prefetch: bool,
        aec_processor=None,
        tts_map: dict[str, object] | None = None,
        printer: Callable[[str], None] = print,
    ) -> None:
        self.pending_turn = PendingSpeechTurnBuffer()
        self.conversation = None
        self.is_probable_tts_echo = is_probable_tts_echo
        self.machine = machine
        self.speech_gate = speech_gate
        self.handle_user_text = handle_user_text
        self.prefetch = prefetch
        self.aec_processor = aec_processor
        self.tts_map = tts_map or {}
        self.printer = printer

    def maybe_flush_user_turn(self, force: bool = False) -> None:
        flush_multi_speech_turn(
            pending_turn=self.pending_turn,
            force=force,
            is_probable_tts_echo=self.is_probable_tts_echo,
            conversation=self.conversation,
            machine=self.machine,
            speech_gate=self.speech_gate,
            aec_processor=self.aec_processor,
            handle_user_text=self.handle_user_text,
            prefetch=self.prefetch,
            printer=self.printer,
        )

    def on_user_utterance(self, text: str) -> None:
        queue_multi_user_utterance(
            text=text,
            pending_turn=self.pending_turn,
            is_probable_tts_echo=self.is_probable_tts_echo,
            conversation=self.conversation,
            machine=self.machine,
            printer=self.printer,
        )

    def create_conversation(self, *, recognizer):
        self.conversation = create_default_conversation(
            recognizer=recognizer,
            machine=self.machine,
            on_user_utterance=self.on_user_utterance,
            on_partial=create_partial_handler(
                is_probable_tts_echo=self.is_probable_tts_echo,
                machine=self.machine,
            ),
        )
        return self.conversation

    def start_worker(self, *, device: int | None) -> threading.Thread:
        return start_default_worker(
            machine=self.machine,
            device=device,
            conversation=self.conversation,
            aec_processor=self.aec_processor,
            tts_map=self.tts_map,
            pause_during_tts=False,
            flush_callback=self.maybe_flush_user_turn,
            error_source="stt_multi",
            print_error=self.printer,
        )


def create_multi_speech_runtime(**kwargs) -> MultiSpeechInputRuntime:
    return MultiSpeechInputRuntime(**kwargs)


def create_multi_speech_runtime_from_outputs(
    *,
    output_resources,
    machine,
    speech_gate,
    handle_user_text: Callable[..., None],
    prefetch: bool,
    printer: Callable[[str], None] = print,
) -> MultiSpeechInputRuntime:
    return create_multi_speech_runtime(
        is_probable_tts_echo=output_resources.is_probable_tts_echo,
        machine=machine,
        speech_gate=speech_gate,
        handle_user_text=handle_user_text,
        prefetch=prefetch,
        aec_processor=output_resources.aec_processor,
        tts_map=output_resources.tts_map,
        printer=printer,
    )
