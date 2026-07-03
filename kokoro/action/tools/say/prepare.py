"""Preparation stage for say action tools."""

from __future__ import annotations

import time

from kokoro.action import dialogue_orchestrator as dialogue_mod
from kokoro.action import model as action_model
from kokoro.action import tool_spec
from kokoro.action.tools.say import runtime


def prepare_say(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    resources = runtime.resources_from_context(ctx)
    args = dict(action.args)
    text = str(args.get("user_text") or args.get("text") or "").strip()
    decision = args.get("decision")
    if not isinstance(decision, dialogue_mod.DialogueDecision):
        decision = dialogue_mod.DialogueDecision(
            action="speak",
            intent=str(args.get("intent") or "回应"),
            utterance_mode=str(args.get("utterance_mode") or "normal"),
            context_use=str(args.get("context_use") or "none"),
        )
    extra_context = str(args.get("extra_context") or "").strip() or None
    max_history_messages = args.get("max_history_messages")
    messages = resources.dialogue.build_reply_messages(
        user_text=text,
        decision=decision,
        extra_context=extra_context,
        max_history_messages=max_history_messages if isinstance(max_history_messages, int) else None,
    )
    if bool(args.get("stt_refine_inline", False)):
        from kokoro.core import prompts

        inline_prompt = prompts.get("stt_refine_inline.system", "")
        if inline_prompt:
            messages.insert(-1, {"role": "system", "content": inline_prompt})
    args.update(
        {
            "user_text": text,
            "decision": decision,
            "messages": messages,
            "cancel_event": runtime.cancel_event_for(resources, action),
            "trace_t0": time.perf_counter(),
            "streamed": True,
        }
    )
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=action.reason or "prepare say action",
        metadata={"prepared_by": "say.prepare_say"},
    )


def prepare_precomputed(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    resources = runtime.resources_from_context(ctx)
    reply = dialogue_mod.clean_generated_reply(
        str(action.args.get("reply") or ""),
        resources.session.character_name,
    )
    args = dict(action.args)
    args.update(
        {
            "user_text": str(args.get("user_text") or "").strip(),
            "reply": reply,
            "cancel_event": runtime.cancel_event_for(resources, action),
            "streamed": False,
        }
    )
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=action.reason or "prepare precomputed say action",
        metadata={"prepared_by": "say.prepare_precomputed"},
    )


def prepare_wait(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    args = dict(action.args)
    args["reason"] = str(args.get("reason") or "").strip() or action.reason or "wait"
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=args["reason"],
        metadata={"prepared_by": "say.prepare_wait"},
    )
