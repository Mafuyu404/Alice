"""Speech output and say action resource bundles."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable


@dataclass
class SayRuntimeResources:
    session: object
    dialogue: object
    dialogue_model: str
    memory_backend: object
    machine: object
    tts_engine: object | None = None
    subtitle_client: object | None = None
    portrait_worker: object | None = None
    conversation: object | None = None
    agent_config: object | None = None
    task_manager: object | None = None
    vts_controller: object | None = None
    vts_arbiter: object | None = None
    vts_body_driver: object | None = None
    event_loop: object | None = None
    qq_send_message: Callable | None = None
    remember_tts_text: Callable[[str], None] | None = None
    aec_processor: object | None = None
    cancel_slot: list[threading.Event | None] | None = None


@dataclass
class SingleOutputResources:
    tts_engine: object | None
    aec_processor: object | None
    portrait_client: object | None
    portrait_worker: object | None
    subtitle_client: object | None
    stt_subtitle_client: object | None
    remember_tts_text: Callable[[str], None]
    is_probable_tts_echo: Callable[[str], bool]


@dataclass
class MultiOutputResources:
    tts_map: dict[str, object]
    aec_processor: object | None
    portrait_clients: dict[str, object]
    portrait_workers: dict[str, object]
    remember_tts_text: Callable[[str], None]
    is_probable_tts_echo: Callable[[str], bool]


def create_single_output_resources(
    *,
    tts_enabled: bool,
    voice_id: str | None,
    aec_enabled: bool,
    portrait_enabled: bool,
    character_id: str,
    dialogue_model: str,
    machine,
    config: dict,
) -> SingleOutputResources:
    from kokoro.action.tools.say import aec as aec_mod
    from kokoro.action.tools.say import echo_filter as echo_filter_mod
    from kokoro.action.tools.say import portrait_controller
    from kokoro.action.tools.say import subtitle as subtitle_mod
    from kokoro.action.tools.say import tts as tts_mod

    tts_engine = tts_mod.create_prepared_engine(tts_enabled, voice_id)
    aec_result = aec_mod.install_default_aec(
        enabled=aec_enabled,
        tts_engines=[tts_engine],
    )
    portrait_client, portrait_worker = portrait_controller.create_default_controller(
        enabled=portrait_enabled,
        character_id=character_id,
        model=dialogue_model,
        machine=machine,
    )
    subtitle_client, stt_subtitle_client = subtitle_mod.create_default_clients(
        enabled=portrait_enabled,
        config=config,
    )
    echo_filter = echo_filter_mod.create_default_filter()
    return SingleOutputResources(
        tts_engine=tts_engine,
        aec_processor=aec_result.processor,
        portrait_client=portrait_client,
        portrait_worker=portrait_worker,
        subtitle_client=subtitle_client,
        stt_subtitle_client=stt_subtitle_client,
        remember_tts_text=echo_filter.remember,
        is_probable_tts_echo=echo_filter.is_probable_echo,
    )


def create_multi_output_resources(
    *,
    character_ids: list[str],
    characters: dict,
    tts_enabled: bool,
    aec_enabled: bool,
    portrait_enabled: bool,
    model: str,
    config: dict,
    printer=print,
) -> MultiOutputResources:
    from kokoro.action.tools.say import aec as aec_mod
    from kokoro.action.tools.say import echo_filter as echo_filter_mod
    from kokoro.action.tools.say import portrait_controller
    from kokoro.action.tools.say import tts as tts_mod

    tts_map = tts_mod.create_engines_for_characters(
        character_ids,
        characters,
        enabled=tts_enabled,
        printer=printer,
    )
    aec_result = aec_mod.install_default_aec(
        enabled=aec_enabled,
        tts_engines=tts_map.values(),
        printer=printer,
    )
    portrait_clients, portrait_workers = portrait_controller.create_multi_controllers(
        enabled=portrait_enabled,
        character_ids=character_ids,
        model=model,
        config=config,
        printer=printer,
    )
    echo_filter = echo_filter_mod.create_default_filter()
    return MultiOutputResources(
        tts_map=tts_map,
        aec_processor=aec_result.processor,
        portrait_clients=portrait_clients,
        portrait_workers=portrait_workers,
        remember_tts_text=echo_filter.remember,
        is_probable_tts_echo=echo_filter.is_probable_echo,
    )


def create_resources(
    *,
    session: object,
    dialogue: object,
    dialogue_model: str,
    memory_backend: object,
    machine: object,
    tts_engine: object | None = None,
    subtitle_client: object | None = None,
    portrait_worker: object | None = None,
    conversation: object | None = None,
    agent_config: object | None = None,
    task_manager: object | None = None,
    vts_controller: object | None = None,
    vts_arbiter: object | None = None,
    vts_body_driver: object | None = None,
    event_loop: object | None = None,
    qq_send_message: Callable | None = None,
    remember_tts_text: Callable[[str], None] | None = None,
    aec_processor: object | None = None,
    cancel_slot: list[threading.Event | None] | None = None,
) -> SayRuntimeResources:
    return SayRuntimeResources(
        session=session,
        dialogue=dialogue,
        dialogue_model=dialogue_model,
        memory_backend=memory_backend,
        machine=machine,
        tts_engine=tts_engine,
        subtitle_client=subtitle_client,
        portrait_worker=portrait_worker,
        conversation=conversation,
        agent_config=agent_config,
        task_manager=task_manager,
        vts_controller=vts_controller,
        vts_arbiter=vts_arbiter,
        vts_body_driver=vts_body_driver,
        event_loop=event_loop,
        qq_send_message=qq_send_message,
        remember_tts_text=remember_tts_text,
        aec_processor=aec_processor,
        cancel_slot=cancel_slot,
    )
