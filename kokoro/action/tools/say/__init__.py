"""Speech/say action tool module."""

from kokoro.action.tools.say.aec import AECInstallResult, AECProcessor, install_aec, install_default_aec
from kokoro.action.tools.say.echo_filter import TTSEchoFilter, create_default_filter, echo_similarity, normalize_echo_text
from kokoro.action.tools.say.portrait_controller import create_default_controller, create_multi_controllers
from kokoro.action.tools.say.runtime import (
    MultiOutputResources,
    SayActionRuntime,
    SayRuntimeResources,
    SingleSayActionBundle,
    SingleOutputResources,
    create_action_runtime,
    create_single_action_bundle,
    create_multi_output_resources,
    create_resources,
    create_single_output_resources,
    execute_dialogue_plan,
    flush_session_outputs,
    print_single_startup_summary,
    print_single_output_startup_summary,
    print_session_greeting,
    shutdown_single_output_resources,
    shutdown_single_runtime,
    start_dialogue_plan_executor,
    start_dialogue_plan_executor_from_outputs,
)
from kokoro.action.tools.say.spec import register
from kokoro.action.tools.say.subtitle import create_default_clients as create_default_subtitle_clients
from kokoro.action.tools.say.tts import (
    SAMPLE_RATE,
    create_engines_for_characters,
    create_prepared_engine,
    say_text,
    wait_for_engines,
)

__all__ = [
    "AECInstallResult",
    "AECProcessor",
    "SAMPLE_RATE",
    "MultiOutputResources",
    "SayActionRuntime",
    "SayRuntimeResources",
    "SingleSayActionBundle",
    "SingleOutputResources",
    "TTSEchoFilter",
    "create_action_runtime",
    "create_single_action_bundle",
    "create_default_controller",
    "create_default_filter",
    "create_default_subtitle_clients",
    "create_engines_for_characters",
    "create_multi_output_resources",
    "create_multi_controllers",
    "create_prepared_engine",
    "create_resources",
    "create_single_output_resources",
    "execute_dialogue_plan",
    "flush_session_outputs",
    "echo_similarity",
    "install_aec",
    "install_default_aec",
    "normalize_echo_text",
    "print_single_startup_summary",
    "print_single_output_startup_summary",
    "print_session_greeting",
    "register",
    "say_text",
    "shutdown_single_output_resources",
    "shutdown_single_runtime",
    "start_dialogue_plan_executor",
    "start_dialogue_plan_executor_from_outputs",
    "wait_for_engines",
]
