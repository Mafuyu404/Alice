"""Execution hooks for say actions."""

from __future__ import annotations

import threading

import requests

from kokoro.action import dialogue_orchestrator as dialogue_mod
from kokoro.action import model as action_model
from kokoro.action import tool_spec
from kokoro.action.tools.say.resources import SayRuntimeResources
from kokoro.action.tools.say.streaming import chat_stream
from kokoro.core import llm_client
from kokoro.core import token_usage


def execute_say(ctx: tool_spec.ToolContext, prepared: tool_spec.PreparedAction) -> tool_spec.ToolResult:
    resources = resources_from_context(ctx)
    text = str(prepared.args.get("user_text") or "").strip()
    messages = prepared.args.get("messages")
    if not isinstance(messages, list):
        return tool_spec.ToolResult(content="say failed: messages not prepared", status="failed")
    cancel_event = prepared.args.get("cancel_event")
    if not isinstance(cancel_event, threading.Event):
        cancel_event = cancel_event_for(resources, prepared.action)
    try:
        reply, cancelled = chat_stream(
            messages,
            resources.session.character_name,
            resources.dialogue_model,
            resources.tts_engine,
            cancel_event=cancel_event,
            character_config=resources.session.character_config,
            agent_config=resources.agent_config,
            usage_callback=token_usage.make_callback(
                resources.dialogue_model,
                str(prepared.args.get("usage_label") or "chat"),
            ),
            tool_context=_tool_context(resources),
            subtitle_client=resources.subtitle_client,
            trace_t0=float(prepared.args.get("trace_t0") or 0.0),
            ai_context_callback=resources.conversation.update_ai_context if resources.conversation else None,
        )
    except requests.exceptions.ConnectionError:
        print(f"\n[connection failed] Cannot connect to {llm_client.api_base_for(resources.dialogue_model)}")
        resources.machine.emit_error("llm_connection")
        return tool_spec.ToolResult(content="say failed: llm connection", status="failed")

    if cancelled:
        return tool_spec.ToolResult(content="say cancelled", status="cancelled")

    reply = dialogue_mod.clean_generated_reply(reply, resources.session.character_name)
    return tool_spec.ToolResult(
        content=reply or "say completed",
        status="success",
        metadata={
            "finish_speech": True,
            "text": text,
            "reply": reply,
            "cancel_event": cancel_event,
            "streamed": True,
        },
    )


def execute_say_precomputed(ctx: tool_spec.ToolContext, prepared: tool_spec.PreparedAction) -> tool_spec.ToolResult:
    resources = resources_from_context(ctx)
    text = str(prepared.args.get("user_text") or "").strip()
    reply = str(prepared.args.get("reply") or "")
    cancel_event = prepared.args.get("cancel_event")
    if not isinstance(cancel_event, threading.Event):
        cancel_event = cancel_event_for(resources, prepared.action)
    return tool_spec.ToolResult(
        content=reply or "say completed",
        status="success",
        metadata={
            "finish_speech": True,
            "text": text,
            "reply": reply,
            "cancel_event": cancel_event,
            "streamed": False,
        },
    )


def execute_wait(ctx: tool_spec.ToolContext, prepared: tool_spec.PreparedAction) -> tool_spec.ToolResult:
    reason = str(prepared.args.get("reason") or "").strip() or prepared.action.reason or "wait"
    return tool_spec.ToolResult(content=f"wait: {reason}", status="success")


def cancel_event_for(resources: SayRuntimeResources, action: action_model.Action) -> threading.Event:
    cancel_event = action.args.get("cancel_event")
    if not isinstance(cancel_event, threading.Event):
        cancel_event = threading.Event()
    if resources.cancel_slot is not None:
        resources.cancel_slot[0] = cancel_event
    return cancel_event


def resources_from_context(ctx: tool_spec.ToolContext) -> SayRuntimeResources:
    resources = ctx.get("say_resources")
    if not isinstance(resources, SayRuntimeResources):
        raise TypeError("say_resources must be SayRuntimeResources")
    return resources


def _tool_context(resources: SayRuntimeResources) -> dict:
    return {
        "session": resources.session,
        "memory_backend": resources.memory_backend,
        "character_id": resources.session.character_id,
        "vts_controller": resources.vts_controller,
        "vts_arbiter": resources.vts_arbiter,
        "vts_body_driver": resources.vts_body_driver,
        "event_loop": resources.event_loop,
        "task_manager": resources.task_manager,
        "qq_send_message": resources.qq_send_message,
    }
