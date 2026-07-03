"""Public facade for multi-character CLI runtime helpers."""

from kokoro.action.tools.multi_relay.lifecycle import (
    MultiCliRuntimeBundle,
    create_cli_runtime,
    shutdown_cli_runtime_bundle,
    shutdown_runtime,
    shutdown_runtime_outputs,
    start_cli_runtime,
)
from kokoro.action.tools.multi_relay.session import MultiSessionRuntime, load_session_runtime
from kokoro.action.tools.multi_relay.speech import (
    MultiDialogueRuntime,
    MultiSpeechResources,
    MultiTurnPredictor,
    SpeechGate,
    create_dialogue_runtime,
    create_speech_resources,
    create_state_machine,
    make_thread_safe_printer,
    play_auto_cycle,
    play_single_auto_turn,
    play_turn,
    print_output_startup_summary,
    print_startup_summary,
    run_auto_turns,
    save_chat_logs,
)
from kokoro.action.tools.multi_relay.transports import MultiToolTransports, start_transports

__all__ = [
    "MultiCliRuntimeBundle",
    "MultiDialogueRuntime",
    "MultiSessionRuntime",
    "MultiSpeechResources",
    "MultiToolTransports",
    "MultiTurnPredictor",
    "SpeechGate",
    "create_cli_runtime",
    "create_dialogue_runtime",
    "create_speech_resources",
    "create_state_machine",
    "load_session_runtime",
    "make_thread_safe_printer",
    "play_auto_cycle",
    "play_single_auto_turn",
    "play_turn",
    "print_output_startup_summary",
    "print_startup_summary",
    "run_auto_turns",
    "save_chat_logs",
    "shutdown_cli_runtime_bundle",
    "shutdown_runtime",
    "shutdown_runtime_outputs",
    "start_cli_runtime",
    "start_transports",
]
