"""Multi-character CLI runtime lifecycle bundle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from kokoro.action.tools.multi_relay.session import MultiSessionRuntime, load_session_runtime
from kokoro.action.tools.multi_relay.speech import (
    MultiTurnPredictor,
    SpeechGate,
    create_dialogue_runtime,
    create_speech_resources,
    create_state_machine,
    make_thread_safe_printer,
    print_output_startup_summary,
)
from kokoro.action.tools.multi_relay.transports import MultiToolTransports, start_transports


@dataclass
class MultiCliRuntimeBundle:
    character_ids: list[str]
    session_runtime: MultiSessionRuntime
    transports: MultiToolTransports
    machine: object
    printer: Callable[..., None]
    speech_gate: object
    output_resources: object
    prediction: object
    speech_resources: object
    dialogue_runtime: object
    speech_input_runtime: object
    speech_input_startup: object
    conversation: object | None = None

    @property
    def orchestrator(self):
        return self.session_runtime.orchestrator


def shutdown_runtime(
    *,
    machine,
    tts_map: dict[str, object],
    portrait_workers: dict[str, object],
    portrait_clients: dict[str, object],
    orchestrator,
    web_search_runtime,
    printer: Callable[[str], None] = print,
) -> None:
    try:
        from kokoro.core import state_machine as sm

        machine.emit(sm.SystemEvent.SHUTDOWN)
    except Exception:
        pass
    for engine in tts_map.values():
        try:
            engine.close()
        except Exception:
            pass
    for worker in portrait_workers.values():
        try:
            worker.stop()
        except Exception:
            pass
    for client in portrait_clients.values():
        try:
            client.shutdown()
        except Exception:
            pass
    try:
        orchestrator.close()
    except Exception:
        pass
    try:
        web_search_runtime.stop()
    except Exception:
        pass
    for cid, session in getattr(orchestrator, "sessions", {}).items():
        try:
            path = session.write_chat_log_to_file()
            if path:
                printer(f"  [chat_log] {session.character_name} saved to {path}")
        except Exception as exc:
            printer(f"  [chat_log] {cid} failed: {exc}")


def shutdown_runtime_outputs(
    *,
    machine,
    output_resources,
    orchestrator,
    transports: MultiToolTransports,
    printer: Callable[[str], None] = print,
) -> None:
    shutdown_runtime(
        machine=machine,
        tts_map=output_resources.tts_map,
        portrait_workers=output_resources.portrait_workers,
        portrait_clients=output_resources.portrait_clients,
        orchestrator=orchestrator,
        web_search_runtime=transports.web_search_runtime,
        printer=printer,
    )


def create_cli_runtime(
    *,
    args,
    root,
    cli_config: dict,
    printer=print,
) -> MultiCliRuntimeBundle | None:
    from kokoro.action.tools import say as say_tool
    from kokoro.action.tools import speech_input as speech_input_tool

    character_ids = [c.strip() for c in args.multi.split(",") if c.strip()]
    if len(character_ids) < 2:
        printer("[error] --multi needs at least 2 character IDs")
        return None

    session_runtime = load_session_runtime(
        character_ids=character_ids,
        model_override=args.model,
    )
    safe_print = make_thread_safe_printer()
    transports = start_transports(config=session_runtime.runtime_config, root=root)
    machine = create_state_machine()
    speech_gate = SpeechGate()
    output_resources = say_tool.create_multi_output_resources(
        character_ids=character_ids,
        characters=session_runtime.characters,
        tts_enabled=not args.no_tts,
        aec_enabled=session_runtime.aec_enabled,
        portrait_enabled=not args.no_portrait,
        model=session_runtime.model,
        config=session_runtime.runtime_config,
        printer=safe_print,
    )
    prediction = MultiTurnPredictor(
        orchestrator=session_runtime.orchestrator,
        enabled=lambda: bool(args.watch),
        printer=safe_print,
    )
    speech_resources = create_speech_resources(
        output_resources=output_resources,
        say_text=say_tool.say_text,
        printer=safe_print,
    )
    dialogue_runtime = create_dialogue_runtime(
        orchestrator=session_runtime.orchestrator,
        user_name=session_runtime.user_name,
        speech_gate=speech_gate,
        prediction=prediction,
        resources=speech_resources,
        watch_enabled=lambda: bool(args.watch),
        idle_seconds=float(args.idle_seconds),
        wait_for_engines=say_tool.wait_for_engines,
        printer=safe_print,
    )
    speech_input_runtime = speech_input_tool.create_multi_speech_runtime_from_outputs(
        output_resources=output_resources,
        machine=machine,
        speech_gate=speech_gate,
        handle_user_text=dialogue_runtime.handle_user_text,
        prefetch=args.watch,
        printer=safe_print,
    )
    speech_input_startup = speech_input_tool.prepare_default_input(
        enabled=session_runtime.stt_enabled,
        device_arg=args.device,
        config=cli_config,
        printer=safe_print,
    )
    if session_runtime.stt_enabled and speech_input_startup.recognizer is None:
        return None

    conversation = None
    if session_runtime.stt_enabled:
        conversation = speech_input_runtime.create_conversation(recognizer=speech_input_startup.recognizer)

    return MultiCliRuntimeBundle(
        character_ids=character_ids,
        session_runtime=session_runtime,
        transports=transports,
        machine=machine,
        printer=safe_print,
        speech_gate=speech_gate,
        output_resources=output_resources,
        prediction=prediction,
        speech_resources=speech_resources,
        dialogue_runtime=dialogue_runtime,
        speech_input_runtime=speech_input_runtime,
        speech_input_startup=speech_input_startup,
        conversation=conversation,
    )


def start_cli_runtime(
    *,
    bundle: MultiCliRuntimeBundle,
    args,
    printer=print,
) -> None:
    print_output_startup_summary(
        names=bundle.session_runtime.names,
        user_name=bundle.session_runtime.user_name,
        output_resources=bundle.output_resources,
        watch=args.watch,
        printer=printer,
    )
    if bundle.session_runtime.stt_enabled and bundle.conversation is not None:
        bundle.speech_input_runtime.start_worker(device=bundle.speech_input_startup.device)

    bundle.dialogue_runtime.run_initial_auto(
        rounds=args.auto,
        topic=args.topic,
        prefetch=args.watch,
    )
    if args.watch:
        bundle.dialogue_runtime.run_watch(
            auto_rounds=args.auto,
            topic=args.topic,
            max_turns=args.max_turns,
        )
        return
    bundle.dialogue_runtime.run_interactive()


def shutdown_cli_runtime_bundle(
    bundle: MultiCliRuntimeBundle,
    *,
    printer=print,
) -> None:
    shutdown_runtime_outputs(
        machine=bundle.machine,
        output_resources=bundle.output_resources,
        orchestrator=bundle.orchestrator,
        transports=bundle.transports,
        printer=printer,
    )
