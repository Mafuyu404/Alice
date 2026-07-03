"""Public facade for multi-character speech runtime helpers."""

from kokoro.action.tools.multi_relay.dialogue import MultiDialogueRuntime, create_dialogue_runtime
from kokoro.action.tools.multi_relay.playback import (
    MultiSpeechResources,
    create_speech_resources,
    play_auto_cycle,
    play_single_auto_turn,
    play_turn,
    print_output_startup_summary,
    print_startup_summary,
    run_auto_turns,
    save_chat_logs,
)
from kokoro.action.tools.multi_relay.prediction import MultiTurnPredictor
from kokoro.action.tools.multi_relay.primitives import SpeechGate, create_state_machine, make_thread_safe_printer

__all__ = [
    "MultiDialogueRuntime",
    "MultiSpeechResources",
    "MultiTurnPredictor",
    "SpeechGate",
    "create_dialogue_runtime",
    "create_speech_resources",
    "create_state_machine",
    "make_thread_safe_printer",
    "play_auto_cycle",
    "play_single_auto_turn",
    "play_turn",
    "print_output_startup_summary",
    "print_startup_summary",
    "run_auto_turns",
    "save_chat_logs",
]
