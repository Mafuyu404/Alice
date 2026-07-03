"""Startup, shutdown, and output lifecycle helpers for say runtime."""

from __future__ import annotations

from kokoro.action.tools.say.resources import SingleOutputResources


def flush_session_outputs(session, *, printer=print) -> None:
    if session is not None and getattr(session, "history", None):
        try:
            log_path = session.write_chat_log_to_file()
            if log_path:
                printer(f"  [chat_log] saved to {log_path}")
        except Exception as exc:
            printer(f"  [chat_log] failed: {exc}")

    memory_events = getattr(session, "memory_events", None) if session is not None else None
    if memory_events is not None:
        memory_events.flush_all(
            user_name=session.user_name,
            character_name=session.character_name,
            summary=session.summary or "",
        )


def shutdown_single_runtime(
    *,
    live_runtime=None,
    qq_bridge=None,
    debug_runtime=None,
    web_search_runtime=None,
    portrait_worker=None,
    portrait_client=None,
    subtitle_client=None,
    stt_subtitle_client=None,
    tts_engine=None,
    action_runtime=None,
    tool_runtime=None,
    vts_runtime=None,
) -> None:
    for runtime_obj in (live_runtime, qq_bridge, debug_runtime, web_search_runtime):
        _call_noarg(runtime_obj, "stop")
    for runtime_obj, method in (
        (portrait_worker, "stop"),
        (portrait_client, "shutdown"),
        (subtitle_client, "shutdown"),
        (stt_subtitle_client, "shutdown"),
        (tts_engine, "close"),
        (action_runtime, "shutdown"),
        (tool_runtime, "shutdown"),
        (vts_runtime, "shutdown"),
    ):
        _call_noarg(runtime_obj, method)


def _call_noarg(target, method_name: str) -> None:
    if target is None:
        return
    method = getattr(target, method_name, None)
    if not callable(method):
        return
    try:
        method()
    except Exception:
        pass


def shutdown_single_output_resources(
    output_resources: SingleOutputResources,
    *,
    live_runtime=None,
    qq_bridge=None,
    debug_runtime=None,
    web_search_runtime=None,
    action_runtime=None,
    tool_runtime=None,
    vts_runtime=None,
) -> None:
    shutdown_single_runtime(
        live_runtime=live_runtime,
        qq_bridge=qq_bridge,
        debug_runtime=debug_runtime,
        web_search_runtime=web_search_runtime,
        portrait_worker=output_resources.portrait_worker,
        portrait_client=output_resources.portrait_client,
        subtitle_client=output_resources.subtitle_client,
        stt_subtitle_client=output_resources.stt_subtitle_client,
        tts_engine=output_resources.tts_engine,
        action_runtime=action_runtime,
        tool_runtime=tool_runtime,
        vts_runtime=vts_runtime,
    )


def print_single_startup_summary(
    *,
    session,
    dialogue_model: str,
    stt_enabled: bool,
    device,
    tts_engine=None,
    portrait_worker=None,
    vts_runtime=None,
    use_proactive: bool,
    tool_enabled: bool,
    background_runtime=None,
    live_runtime=None,
    subtitle_client=None,
    stt_subtitle_client=None,
    aec_processor=None,
    printer=print,
) -> None:
    printer()
    printer("=" * 50)
    printer("  Alice CLI")
    printer(f"  Character: {session.character_name}")
    printer(f"  Dialogue model: {dialogue_model}")
    printer(f"  Microphone: [{'disabled' if not stt_enabled else device}]")
    printer(f"  TTS: {tts_engine is not None}")
    printer(f"  Portrait: {portrait_worker is not None}")
    printer(f"  VTS: {bool(getattr(vts_runtime, 'connected', False))}")
    printer(f"  Proactive dialogue: {use_proactive}")
    printer(f"  Tool calling: {tool_enabled}")
    printer(f"  Screen watch: {bool(getattr(background_runtime, 'screen_watch_enabled', False))}")
    printer(f"  Edge page cache: {bool(getattr(background_runtime, 'edge_cache_enabled', False))}")
    printer(f"  Memory events: {bool(getattr(background_runtime, 'memory_events_enabled', False))}")
    live_active = bool(getattr(live_runtime, "bilibili_active", False))
    live_mode = getattr(live_runtime, "live_mode", False)
    printer(f"  Bilibili live: {live_active} (live_mode={live_mode})")
    printer(f"  Subtitle: {subtitle_client is not None} | STT subtitle: {stt_subtitle_client is not None}")
    printer("  AEC: " + ("enabled" if aec_processor is not None else "disabled"))
    printer("  Ctrl+C to stop")
    printer("=" * 50)


def print_single_output_startup_summary(
    *,
    session,
    dialogue_model: str,
    stt_enabled: bool,
    device,
    output_resources: SingleOutputResources,
    vts_runtime=None,
    use_proactive: bool,
    tool_enabled: bool,
    background_runtime=None,
    live_runtime=None,
    printer=print,
) -> None:
    print_single_startup_summary(
        session=session,
        dialogue_model=dialogue_model,
        stt_enabled=stt_enabled,
        device=device,
        tts_engine=output_resources.tts_engine,
        portrait_worker=output_resources.portrait_worker,
        vts_runtime=vts_runtime,
        use_proactive=use_proactive,
        tool_enabled=tool_enabled,
        background_runtime=background_runtime,
        live_runtime=live_runtime,
        subtitle_client=output_resources.subtitle_client,
        stt_subtitle_client=output_resources.stt_subtitle_client,
        aec_processor=output_resources.aec_processor,
        printer=printer,
    )


def print_session_greeting(session, *, printer=print) -> None:
    greeting = session.character_data.get("greeting")
    if greeting:
        printer(f"\n{session.character_name}: {greeting}")
