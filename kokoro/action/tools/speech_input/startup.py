"""STT startup and conversation construction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from kokoro.action.tools.speech_input import conversation as conversation_mod
from kokoro.action.tools.speech_input import stt as stt_mod
from kokoro.core import config as cfg


@dataclass(frozen=True)
class SpeechInputStartup:
    enabled: bool
    device: int | None
    recognizer: object | None


def prepare_default_input(
    *,
    enabled: bool,
    device_arg: int | None,
    config: dict,
    printer: Callable[[str], None] = print,
) -> SpeechInputStartup:
    device = stt_mod.resolve_input_device(enabled, device_arg)
    if not enabled:
        printer("  [stt] disabled by config")
        return SpeechInputStartup(enabled=False, device=device, recognizer=None)
    if device is None:
        printer("\n[error] No microphone device found.")
        printer("Run `python cli.py --list-devices` to inspect available devices.\n")
        return SpeechInputStartup(enabled=True, device=None, recognizer=None)
    recognizer = stt_mod.create_default_recognizer(config, printer=printer)
    return SpeechInputStartup(enabled=True, device=device, recognizer=recognizer)


def create_default_conversation(
    *,
    recognizer,
    machine,
    on_user_utterance: Callable[[str], None],
    on_partial: Callable[[str], None],
):
    return conversation_mod.ConversationManager(
        recognizer=recognizer,
        machine=machine,
        on_user_utterance=on_user_utterance,
        on_partial=on_partial,
        sample_rate=stt_mod.SAMPLE_RATE,
        silence_endpoint_delay=cfg.stt_refine_stable_seconds(),
        commit_delay=cfg.stt_utterance_commit_seconds(),
        short_extra_delay=cfg.stt_short_utterance_extra_seconds(),
        short_max_chars=cfg.stt_short_utterance_max_chars(),
    )
