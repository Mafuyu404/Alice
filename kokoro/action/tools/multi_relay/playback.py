"""Playback and summary helpers for multi-character CLI runtime."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from kokoro.action.tools.multi_relay.prediction import MultiTurnPredictor
from kokoro.action.tools.multi_relay.primitives import SpeechGate


@dataclass
class MultiSpeechResources:
    tts_map: dict[str, object]
    portrait_workers: dict[str, object]
    remember_tts_text: Callable[[str], None]
    say_text: Callable[..., None]
    printer: Callable[..., None]


def create_speech_resources(
    *,
    output_resources,
    say_text: Callable[..., None],
    printer: Callable[..., None] = print,
) -> MultiSpeechResources:
    return MultiSpeechResources(
        tts_map=output_resources.tts_map,
        portrait_workers=output_resources.portrait_workers,
        remember_tts_text=output_resources.remember_tts_text,
        say_text=say_text,
        printer=printer,
    )


def print_startup_summary(
    *,
    names: dict[str, str],
    user_name: str,
    tts_map: dict[str, object],
    aec_enabled: bool,
    watch: bool,
    printer: Callable[..., None] = print,
) -> None:
    printer("=" * 50)
    printer("  Multi-Character Chat")
    for cid, cname in names.items():
        tts_on = "on" if tts_map.get(cid) else "off"
        printer("  " + cid + " -> " + cname + "  [tts:" + tts_on + "]")
    printer("  User: " + user_name)
    printer("  Voice input: on")
    printer("  AEC: " + ("enabled" if aec_enabled else "disabled"))
    if watch:
        printer("  Mode: watch (unattended)")
        printer("  Stop: Ctrl+C")
    else:
        printer("  Commands: /exit, /auto N, /watch [N]")
        printer("  Empty input = auto next turn")
    printer("=" * 50)


def print_output_startup_summary(
    *,
    names: dict[str, str],
    user_name: str,
    output_resources,
    watch: bool,
    printer: Callable[..., None] = print,
) -> None:
    print_startup_summary(
        names=names,
        user_name=user_name,
        tts_map=output_resources.tts_map,
        aec_enabled=output_resources.aec_processor is not None,
        watch=watch,
        printer=printer,
    )


def play_turn(
    cid,
    cname,
    reply,
    *,
    resources: MultiSpeechResources,
    prefetch: bool = False,
    start_prediction: Callable[[str, str, str], None] | None = None,
) -> None:
    if not reply:
        return
    resources.printer()
    resources.printer(str(cname) + "> " + str(reply))
    resources.remember_tts_text(str(reply))
    portrait_worker = resources.portrait_workers.get(cid)
    if portrait_worker:
        portrait_worker.submit("", reply)
    engine = resources.tts_map.get(cid)
    if engine:
        resources.say_text(engine, reply, wait=True)
    if prefetch and start_prediction is not None:
        start_prediction(str(cid), str(cname), str(reply))


def run_auto_turns(
    *,
    limit: int,
    idle_seconds: float,
    sleep_between: bool,
    orchestrator,
    prediction: MultiTurnPredictor,
    speech_gate: SpeechGate,
    wait_for_engines: Callable[[dict[str, object]], None],
    tts_map: dict[str, object],
    play: Callable[[object, object, object, bool], None],
) -> int:
    produced = 0
    while limit <= 0 or produced < limit:
        if sleep_between and produced > 0:
            time.sleep(max(0.1, float(idle_seconds)))
        if speech_gate.blocked():
            time.sleep(0.1)
            continue
        wait_for_engines(tts_map)
        page_changed = bool(getattr(orchestrator, "consume_random_mc_page_change", lambda: False)())
        if page_changed:
            prediction.clear()
            cid, cname, reply = orchestrator.auto_turn()
        else:
            cid, cname, reply = prediction.take(timeout=0.5)
            if not reply:
                cid, cname, reply = orchestrator.auto_turn()
        if not reply and getattr(orchestrator, "last_auto_action", "") in ("silence", "observe", "cancel_plan"):
            prediction.clear()
        elif not reply:
            cid, cname, reply = prediction.take()
        if not reply:
            if limit > 0:
                break
            time.sleep(max(0.5, float(idle_seconds)))
            continue
        play(cid, cname, reply, True)
        produced += 1
    return produced


def play_auto_cycle(
    *,
    orchestrator,
    rounds: int,
    play: Callable[[object, object, object, bool], None],
    prefetch: bool,
) -> None:
    for cid, cname, reply in orchestrator.auto_cycle(rounds=rounds):
        play(cid, cname, reply, prefetch)


def play_single_auto_turn(
    *,
    orchestrator,
    prediction: MultiTurnPredictor,
    play: Callable[[object, object, object, bool], None],
    prefetch: bool = False,
) -> None:
    prediction.clear()
    cid, cname, reply = orchestrator.auto_turn()
    play(cid, cname, reply, prefetch)


def save_chat_logs(orchestrator, *, printer: Callable[[str], None] = print) -> None:
    for _cid, session in getattr(orchestrator, "sessions", {}).items():
        path = session.write_chat_log_to_file()
        if path:
            printer(f"  [chat log saved] {path}")
