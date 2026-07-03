"""Post-execution hooks for say action tools."""

from __future__ import annotations

import threading
import time

from kokoro.action import tool_spec
from kokoro.action.tools.say import tts as tts_mod
from kokoro.action.tools.say import runtime
from kokoro.core import config as cfg


def after_spoken_reply(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
    result: tool_spec.ToolResult,
) -> None:
    if result.status not in {"success", "cancelled"}:
        return
    metadata = result.metadata
    if not metadata.get("finish_speech"):
        return
    resources = runtime.resources_from_context(ctx)
    text = str(metadata.get("text") or prepared.args.get("user_text") or "").strip()
    reply = str(metadata.get("reply") or result.content or "")
    streamed = bool(metadata.get("streamed", prepared.args.get("streamed", True)))
    cancel_event = metadata.get("cancel_event") or prepared.args.get("cancel_event")
    if not isinstance(cancel_event, threading.Event):
        cancel_event = threading.Event()
    status = finish_spoken_reply(
        resources,
        text=text,
        reply=reply,
        cancel_event=cancel_event,
        streamed=streamed,
    )
    result.metadata["finish_status"] = status
    if status == "say cancelled during tts":
        result.status = "cancelled"
        if not result.content:
            result.content = status


def finish_spoken_reply(
    resources: runtime.SayRuntimeResources,
    *,
    text: str,
    reply: str,
    cancel_event: threading.Event,
    streamed: bool,
) -> str:
    from kokoro.core import state_machine as sm

    resources.machine.emit(sm.SystemEvent.LLM_DONE)
    if reply:
        if resources.remember_tts_text:
            resources.remember_tts_text(reply)
        if not streamed:
            print(f"\n{resources.session.character_name}: {reply}")
        if resources.conversation:
            resources.conversation.update_ai_context(reply)
    if resources.portrait_worker:
        resources.portrait_worker.submit(text, reply)
    if resources.tts_engine and reply:
        resources.machine.set_tts_state(sm.TTSState.STREAMING)
        if not streamed:
            tts_mod.say_text(resources.tts_engine, reply, wait=False)
        while resources.tts_engine.is_playing and not cancel_event.is_set():
            time.sleep(0.1)
        if cancel_event.is_set():
            return "say cancelled during tts"
        if resources.aec_processor is not None and cfg.aec_auto_reset_on_tts_done():
            resources.aec_processor.reset()
        resources.tts_engine.prepare()
    if text:
        resources.session.remember(text, reply, async_store=True)
    resources.machine.set_tts_state(sm.TTSState.IDLE)
    resources.machine.emit(sm.SystemEvent.TTS_DONE)
    resources.machine.reset_error_count()
    if resources.subtitle_client:
        resources.subtitle_client.clear()
    return reply or "say completed"
