"""State, policy, and generic runtime helpers for single-character CLI."""

from __future__ import annotations

import threading
import time


def create_state_machine(*, printer=print):
    from kokoro.core import state_machine as sm

    machine = sm.SystemStateMachine()

    def on_state_change(old: sm.SystemState, new: sm.SystemState, event: sm.SystemEvent) -> None:
        if new == sm.SystemState.ERROR:
            printer(f"\n  [state] ERROR (from {old.value} via {event.value})")

    machine.subscribe(on_state_change)
    return machine


def set_proactive_state(machine, *, enabled: bool) -> None:
    from kokoro.core import state_machine as sm

    machine.set_proactive_state(sm.ProactiveState.ACCRUING if enabled else sm.ProactiveState.DISABLED)


def mark_ready(machine) -> None:
    from kokoro.core import state_machine as sm

    machine.emit(sm.SystemEvent.INIT_DONE)


def create_action_policy(
    *,
    config: dict,
    session,
    model: str,
    memory_backend,
):
    from kokoro.action import action_policy

    return action_policy.SingleActionPolicy(
        config=config,
        session=session,
        model=model,
        memory_backend=memory_backend,
    )


def create_tooling_runtime(
    *,
    disabled: bool,
    output_resources=None,
    printer=print,
):
    from kokoro.action import tool_registry as tool_registry_mod

    return tool_registry_mod.create_cli_tooling_runtime(
        disabled=disabled,
        subtitle_client=getattr(output_resources, "subtitle_client", None),
        printer=printer,
    )


def create_cancel_slot() -> list[threading.Event | None]:
    return [None]


def run_until_shutdown(machine, *, sleep_seconds: float = 0.5, printer=print) -> None:
    try:
        while not machine.is_shutting_down:
            time.sleep(max(0.1, float(sleep_seconds)))
    except KeyboardInterrupt:
        printer("\n\n[cli] Stopping...")
