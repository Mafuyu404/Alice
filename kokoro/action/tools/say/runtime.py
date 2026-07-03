"""Public facade for speech action runtime helpers."""

from kokoro.action.tools.say.action_runtime import (
    SayActionRuntime,
    SingleSayActionBundle,
    create_action_runtime,
    create_single_action_bundle,
)
from kokoro.action.tools.say.execution import (
    cancel_event_for,
    execute_say,
    execute_say_precomputed,
    execute_wait,
    resources_from_context,
)
from kokoro.action.tools.say.lifecycle import (
    flush_session_outputs,
    print_session_greeting,
    print_single_output_startup_summary,
    print_single_startup_summary,
    shutdown_single_output_resources,
    shutdown_single_runtime,
)
from kokoro.action.tools.say.proactive import (
    execute_dialogue_plan,
    start_dialogue_plan_executor,
    start_dialogue_plan_executor_from_outputs,
)
from kokoro.action.tools.say.resources import (
    MultiOutputResources,
    SayRuntimeResources,
    SingleOutputResources,
    create_multi_output_resources,
    create_resources,
    create_single_output_resources,
)

__all__ = [
    "MultiOutputResources",
    "SayActionRuntime",
    "SayRuntimeResources",
    "SingleOutputResources",
    "SingleSayActionBundle",
    "cancel_event_for",
    "create_action_runtime",
    "create_multi_output_resources",
    "create_resources",
    "create_single_action_bundle",
    "create_single_output_resources",
    "execute_dialogue_plan",
    "execute_say",
    "execute_say_precomputed",
    "execute_wait",
    "flush_session_outputs",
    "print_session_greeting",
    "print_single_output_startup_summary",
    "print_single_startup_summary",
    "resources_from_context",
    "shutdown_single_output_resources",
    "shutdown_single_runtime",
    "start_dialogue_plan_executor",
    "start_dialogue_plan_executor_from_outputs",
]
