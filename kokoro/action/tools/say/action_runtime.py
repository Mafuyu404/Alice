"""ActionRuntime construction for say actions."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from kokoro.action import runtime as action_runtime
from kokoro.action import tool_spec
from kokoro.action.tools.say.resources import SayRuntimeResources, SingleOutputResources, create_resources


@dataclass
class SayActionRuntime:
    action_runtime: action_runtime.ActionRuntime
    registry: tool_spec.ActionToolRegistry

    def execute_batch(self, batch) -> None:
        self.action_runtime.execute_batch(batch)

    def shutdown(self) -> None:
        self.registry.shutdown()


@dataclass
class SingleSayActionBundle:
    resources: SayRuntimeResources
    action_runtime: SayActionRuntime


def create_action_runtime(
    *,
    session: object,
    resources: SayRuntimeResources,
    merge_window_seconds: float = 0.5,
) -> SayActionRuntime:
    from kokoro.action.tools.say import spec as say_spec

    registry = tool_spec.ActionToolRegistry()
    say_spec.register(registry)
    runtime = action_runtime.ActionRuntime(
        session=session,
        handlers={},
        registry=registry,
        tool_context={"say_resources": resources},
        merge_window_seconds=merge_window_seconds,
    )
    return SayActionRuntime(action_runtime=runtime, registry=registry)


def create_single_action_bundle(
    *,
    session: object,
    dialogue: object,
    dialogue_model: str,
    memory_backend: object,
    machine: object,
    output_resources: SingleOutputResources,
    tooling_runtime,
    transports,
    cancel_slot: list[threading.Event | None],
) -> SingleSayActionBundle:
    resources = create_resources(
        session=session,
        dialogue=dialogue,
        dialogue_model=dialogue_model,
        memory_backend=memory_backend,
        machine=machine,
        tts_engine=output_resources.tts_engine,
        subtitle_client=output_resources.subtitle_client,
        portrait_worker=output_resources.portrait_worker,
        agent_config=tooling_runtime.agent_config,
        task_manager=tooling_runtime.task_manager,
        **transports.vts_say_resource_kwargs(),
        qq_send_message=transports.qq_send_message,
        remember_tts_text=output_resources.remember_tts_text,
        aec_processor=output_resources.aec_processor,
        cancel_slot=cancel_slot,
    )
    return SingleSayActionBundle(
        resources=resources,
        action_runtime=create_action_runtime(
            session=session,
            resources=resources,
        ),
    )
