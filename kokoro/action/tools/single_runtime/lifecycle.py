"""Single-character CLI runtime lifecycle bundle."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from kokoro.action.tools.single_runtime.session import SingleSessionRuntime, load_session_runtime
from kokoro.action.tools.single_runtime.state import (
    create_action_policy,
    create_cancel_slot,
    create_state_machine,
    create_tooling_runtime,
    mark_ready,
    set_proactive_state,
)
from kokoro.action.tools.single_runtime.transports import SingleToolTransports, start_transports


@dataclass
class SingleCliRuntimeBundle:
    machine: object
    session_runtime: SingleSessionRuntime
    output_resources: object
    transports: SingleToolTransports
    tooling_runtime: object
    cancel_slot: list[threading.Event | None]
    dialogue: object
    say_action_bundle: object
    speech_input_runtime: object
    speech_input_startup: object
    background_runtime: object | None
    debug_runtime: object | None = None
    conversation: object | None = None
    dialogue_executor_stop: object | None = None

    @property
    def session(self):
        return self.session_runtime.session

    @property
    def memory_backend(self):
        return self.session_runtime.memory_backend

    @property
    def dialogue_model(self) -> str:
        return self.session_runtime.dialogue_model

    @property
    def action_runtime(self):
        return self.say_action_bundle.action_runtime


def create_cli_runtime(
    *,
    args,
    config: dict,
    root: Path,
    display_user,
    printer=print,
) -> SingleCliRuntimeBundle | None:
    from kokoro.action.tools import say as say_tool
    from kokoro.action.tools import speech_input as speech_input_tool

    machine = create_state_machine(printer=printer)
    session_runtime = load_session_runtime(
        character_id=args.character,
        config=config,
        root=root,
        model_override=args.model,
        no_proactive=args.no_proactive,
        no_stt=args.no_stt,
        printer=printer,
    )
    session = session_runtime.session
    if session is None:
        return None
    life_runtime_primary = _life_runtime_primary(session)

    output_resources = say_tool.create_single_output_resources(
        tts_enabled=not args.no_tts,
        voice_id=session.character_data.get("tts_voice_id"),
        aec_enabled=session_runtime.aec_enabled,
        portrait_enabled=not args.no_portrait,
        character_id=args.character,
        dialogue_model=session_runtime.dialogue_model,
        machine=machine,
        config=config,
    )
    transports = start_transports(
        args=args,
        config=config,
        root=root,
        character_id=args.character,
        session=session,
        output_resources=output_resources,
        machine=machine,
    )
    tooling_runtime = create_tooling_runtime(
        disabled=args.no_tools,
        output_resources=output_resources,
        printer=printer,
    )
    cancel_slot = create_cancel_slot()
    dialogue = create_action_policy(
        config=config,
        session=session,
        model=session_runtime.dialogue_model,
        memory_backend=session_runtime.memory_backend,
    )
    set_proactive_state(machine, enabled=session_runtime.proactive_enabled and not life_runtime_primary)
    say_action_bundle = say_tool.create_single_action_bundle(
        session=session,
        dialogue=dialogue,
        dialogue_model=session_runtime.dialogue_model,
        memory_backend=session_runtime.memory_backend,
        machine=machine,
        output_resources=output_resources,
        tooling_runtime=tooling_runtime,
        transports=transports,
        cancel_slot=cancel_slot,
    )
    life_runtime = getattr(session, "life_runtime", None)
    if life_runtime is not None:
        from kokoro.action.life_runtime import attach_runtime_resources

        attach_runtime_resources(
            life_runtime=life_runtime,
            say_resources=say_action_bundle.resources,
        )
    speech_input_runtime = speech_input_tool.create_single_speech_runtime_from_outputs(
        output_resources=output_resources,
        machine=machine,
        session=session,
        dialogue=dialogue,
        action_runtime=say_action_bundle.action_runtime,
        cancel_slot=cancel_slot,
        transports=transports,
        tool_enabled=tooling_runtime.enabled,
        stt_refine_inline=session_runtime.stt_refine_inline,
        dialogue_pool_enabled=session_runtime.stt_dialogue_pool_enabled,
        display_user=display_user,
        printer=printer,
    )
    dialogue_executor_stop = None
    if not life_runtime_primary:
        dialogue_executor_stop = say_tool.start_dialogue_plan_executor_from_outputs(
            dialogue=dialogue,
            machine=machine,
            action_runtime=say_action_bundle.action_runtime,
            memory_backend=session_runtime.memory_backend,
            session=session,
            cancel_slot=cancel_slot,
            output_resources=output_resources,
            use_proactive=session_runtime.proactive_enabled,
        )
    transports.start_qq()
    background_runtime = transports.start_background(
        machine=machine,
        config=config,
        no_screen_watch=args.no_screen_watch,
        memory_backend=session_runtime.memory_backend,
        session=session,
        output_resources=output_resources,
        dialogue=dialogue,
        use_proactive=lambda: session_runtime.proactive_enabled and not life_runtime_primary,
    )
    speech_input_startup = speech_input_tool.prepare_default_input(
        enabled=session_runtime.stt_enabled,
        device_arg=args.device,
        config=config,
        printer=printer,
    )
    if session_runtime.stt_enabled and speech_input_startup.recognizer is None:
        return None

    conversation = None
    if session_runtime.stt_enabled:
        conversation = speech_input_runtime.create_conversation(recognizer=speech_input_startup.recognizer)
        say_action_bundle.resources.conversation = conversation

    return SingleCliRuntimeBundle(
        machine=machine,
        session_runtime=session_runtime,
        output_resources=output_resources,
        transports=transports,
        tooling_runtime=tooling_runtime,
        cancel_slot=cancel_slot,
        dialogue=dialogue,
        say_action_bundle=say_action_bundle,
        speech_input_runtime=speech_input_runtime,
        speech_input_startup=speech_input_startup,
        background_runtime=background_runtime,
        conversation=conversation,
        dialogue_executor_stop=dialogue_executor_stop,
    )


def start_cli_runtime(
    *,
    bundle: SingleCliRuntimeBundle,
    args,
    config: dict,
    root: Path,
    printer=print,
) -> None:
    from kokoro.action.tools import debug_input
    from kokoro.action.tools import say as say_tool

    say_tool.print_single_output_startup_summary(
        session=bundle.session,
        dialogue_model=bundle.dialogue_model,
        stt_enabled=bundle.session_runtime.stt_enabled,
        device=bundle.speech_input_startup.device,
        output_resources=bundle.output_resources,
        vts_runtime=bundle.transports.vts_runtime,
        use_proactive=bundle.session_runtime.proactive_enabled and not _life_runtime_primary(bundle.session),
        tool_enabled=bundle.tooling_runtime.enabled,
        background_runtime=bundle.background_runtime,
        live_runtime=bundle.transports.live_runtime,
        printer=printer,
    )
    say_tool.print_session_greeting(bundle.session, printer=printer)
    mark_ready(bundle.machine)

    if bundle.session_runtime.stt_enabled and bundle.conversation is not None:
        bundle.speech_input_runtime.start_worker(device=bundle.speech_input_startup.device)
    bundle.debug_runtime = debug_input.start_from_cli(
        args=args,
        config=config,
        root=root,
        session=bundle.session,
        machine=bundle.machine,
        handle_turn=bundle.speech_input_runtime.handle_conversation,
        cancel_slot=bundle.cancel_slot,
        dialogue=bundle.dialogue,
    )


def shutdown_cli_runtime_bundle(
    bundle: SingleCliRuntimeBundle,
    *,
    printer=print,
) -> None:
    shutdown_cli_runtime(
        machine=bundle.machine,
        cancel_slot=bundle.cancel_slot,
        task_manager=bundle.tooling_runtime.task_manager,
        transports=bundle.transports,
        output_resources=bundle.output_resources,
        debug_runtime=bundle.debug_runtime,
        action_runtime=bundle.action_runtime,
        tooling_runtime=bundle.tooling_runtime,
        vts_runtime=bundle.transports.vts_runtime,
        session=bundle.session,
        printer=printer,
    )


def shutdown_cli_runtime(
    *,
    machine,
    cancel_slot: list,
    task_manager,
    transports: SingleToolTransports,
    output_resources,
    debug_runtime=None,
    action_runtime=None,
    tooling_runtime=None,
    vts_runtime=None,
    session=None,
    printer=print,
) -> None:
    from kokoro.action.tools import say as say_tool
    from kokoro.action.tools import task as task_tool
    from kokoro.core import state_machine as sm
    from kokoro.core import token_usage

    machine.emit(sm.SystemEvent.SHUTDOWN)
    cancel = cancel_slot[0] if cancel_slot else None
    if cancel:
        cancel.set()
    task_tool.cancel_all(task_manager, reason="shutdown", printer=printer)
    printer()
    printer(token_usage.summary())
    say_tool.shutdown_single_output_resources(
        output_resources,
        live_runtime=transports.live_runtime,
        qq_bridge=transports.qq_bridge,
        debug_runtime=debug_runtime,
        web_search_runtime=transports.web_search_runtime,
        action_runtime=action_runtime,
        tool_runtime=tooling_runtime,
        vts_runtime=vts_runtime or transports.vts_runtime,
    )
    say_tool.flush_session_outputs(session, printer=printer)


def _life_runtime_primary(session) -> bool:
    life_runtime = getattr(session, "life_runtime", None)
    if life_runtime is None or not bool(getattr(life_runtime, "enabled", False)):
        return False
    section = getattr(life_runtime, "section", {})
    if isinstance(section, dict):
        return bool(section.get("primary", True))
    return True
