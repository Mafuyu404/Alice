"""Proactive dialogue plan execution for say actions."""

from __future__ import annotations

import threading

from kokoro.action.tools.say.action_runtime import SayActionRuntime
from kokoro.action.tools.say.resources import SingleOutputResources
from kokoro.core import prompts


def execute_dialogue_plan(
    *,
    decision,
    machine,
    dialogue,
    action_runtime: SayActionRuntime,
    memory_backend,
    session,
    cancel_slot: list[threading.Event | None],
    subtitle_client=None,
    use_proactive: bool,
) -> None:
    from kokoro.core import state_machine as sm

    if not machine.can_start_conversation:
        dialogue.add_plan(decision, created_from="deferred_busy")
        return
    if not machine.emit(sm.SystemEvent.PROACTIVE_TRIGGERED):
        dialogue.add_plan(decision, created_from="deferred_rejected")
        return

    machine.set_proactive_state(sm.ProactiveState.EXECUTING)
    cancel_event = threading.Event()
    cancel_slot[0] = cancel_event

    try:
        context = ""
        memory_query = " ".join(part for part in (decision.topic, decision.intent) if part)
        if memory_query:
            try:
                memory_ctx = memory_backend.get_context(memory_query, user_id=session.character_id)
            except Exception:
                memory_ctx = ""
            if memory_ctx:
                context = memory_ctx
        continuation_guard = (
            "【续接约束】这是同一场景里稍后补充的一句新话。"
            "不要重复你上一句已经说过的内容，不要重说同一段开场，"
            "直接补充新的后半句、新的信息或新的角度；如果没有新的补充点，就宁可更短。"
        )
        context = f"{continuation_guard}\n\n{context}" if context else continuation_guard
        batch = dialogue.scheduled_say_batch(
            user_text=prompts.get("dialogue_orchestrator.scheduled_user_prompt", "请直接说出现在要说的话。"),
            decision=decision,
            extra_context=context or None,
            cancel_event=cancel_event,
        )
        action_runtime.execute_batch(batch)
        if cancel_event.is_set():
            return
        if subtitle_client:
            subtitle_client.clear()
    finally:
        cancel_slot[0] = None
        machine.set_proactive_state(sm.ProactiveState.ACCRUING if use_proactive else sm.ProactiveState.DISABLED)


def start_dialogue_plan_executor(
    *,
    dialogue,
    machine,
    action_runtime: SayActionRuntime,
    memory_backend,
    session,
    cancel_slot: list[threading.Event | None],
    subtitle_client=None,
    use_proactive: bool,
) -> threading.Event:
    stop_event = threading.Event()

    def execute_fn(decision) -> None:
        execute_dialogue_plan(
            decision=decision,
            machine=machine,
            dialogue=dialogue,
            action_runtime=action_runtime,
            memory_backend=memory_backend,
            session=session,
            cancel_slot=cancel_slot,
            subtitle_client=subtitle_client,
            use_proactive=use_proactive,
        )

    dialogue.start_plan_executor(
        execute_fn=execute_fn,
        cancel_event=stop_event,
    )
    return stop_event


def start_dialogue_plan_executor_from_outputs(
    *,
    dialogue,
    machine,
    action_runtime: SayActionRuntime,
    memory_backend,
    session,
    cancel_slot: list[threading.Event | None],
    output_resources: SingleOutputResources,
    use_proactive: bool,
) -> threading.Event:
    return start_dialogue_plan_executor(
        dialogue=dialogue,
        machine=machine,
        action_runtime=action_runtime,
        memory_backend=memory_backend,
        session=session,
        cancel_slot=cancel_slot,
        subtitle_client=output_resources.subtitle_client,
        use_proactive=use_proactive,
    )
