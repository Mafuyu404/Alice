"""Multi-character dialogue runtime."""

from __future__ import annotations

from collections.abc import Callable

from kokoro.action.tools.multi_relay.playback import (
    MultiSpeechResources,
    play_auto_cycle,
    play_single_auto_turn,
    play_turn,
    run_auto_turns,
    save_chat_logs,
)
from kokoro.action.tools.multi_relay.prediction import MultiTurnPredictor
from kokoro.action.tools.multi_relay.primitives import SpeechGate


class MultiDialogueRuntime:
    def __init__(
        self,
        *,
        orchestrator,
        user_name: str,
        speech_gate: SpeechGate,
        prediction: MultiTurnPredictor,
        resources: MultiSpeechResources,
        watch_enabled: Callable[[], bool],
        idle_seconds: float,
        wait_for_engines: Callable[[dict[str, object]], None],
        printer: Callable[..., None] = print,
        input_fn: Callable[[str], str] = input,
    ) -> None:
        self.orchestrator = orchestrator
        self.user_name = user_name
        self.speech_gate = speech_gate
        self.prediction = prediction
        self.resources = resources
        self.watch_enabled = watch_enabled
        self.idle_seconds = float(idle_seconds)
        self.wait_for_engines = wait_for_engines
        self.printer = printer
        self.input_fn = input_fn

    def play_turn(self, cid, cname, reply, *, prefetch: bool = False) -> None:
        play_turn(
            cid,
            cname,
            reply,
            resources=self.resources,
            prefetch=prefetch,
            start_prediction=self.prediction.start,
        )

    def handle_user_text(self, text: str, *, prefetch: bool | None = None) -> None:
        if not text:
            return
        self.speech_gate.hold(1.2)
        self.prediction.clear()
        self.printer()
        self.printer(self.user_name + "> " + text)
        should_prefetch = self.watch_enabled() if prefetch is None else prefetch
        for cid, cname, reply in self.orchestrator.user_turn(text):
            self.play_turn(cid, cname, reply, prefetch=should_prefetch)

    def run_auto_turns(self, limit: int, *, sleep_between: bool = False) -> int:
        return run_auto_turns(
            limit=limit,
            idle_seconds=self.idle_seconds,
            sleep_between=sleep_between,
            orchestrator=self.orchestrator,
            prediction=self.prediction,
            speech_gate=self.speech_gate,
            wait_for_engines=self.wait_for_engines,
            tts_map=self.resources.tts_map,
            play=lambda cid, cname, reply, prefetch: self.play_turn(
                cid,
                cname,
                reply,
                prefetch=prefetch,
            ),
        )

    def play_auto_cycle(self, *, rounds: int, prefetch: bool) -> None:
        play_auto_cycle(
            orchestrator=self.orchestrator,
            rounds=rounds,
            play=lambda cid, cname, reply, item_prefetch: self.play_turn(
                cid,
                cname,
                reply,
                prefetch=item_prefetch,
            ),
            prefetch=prefetch,
        )

    def play_single_auto_turn(self) -> None:
        play_single_auto_turn(
            orchestrator=self.orchestrator,
            prediction=self.prediction,
            play=lambda cid, cname, reply, prefetch: self.play_turn(
                cid,
                cname,
                reply,
                prefetch=prefetch,
            ),
        )

    def run_initial_auto(self, *, rounds: int, topic: str, prefetch: bool) -> None:
        if rounds <= 0:
            return
        self.printer()
        self.printer("--- Auto " + str(rounds) + " rounds ---")
        if topic:
            self.handle_user_text(topic, prefetch=prefetch)
        self.play_auto_cycle(rounds=rounds, prefetch=prefetch)

    def run_watch(self, *, auto_rounds: int, topic: str, max_turns: int) -> None:
        self.printer()
        self.printer("--- Watch mode ---")
        if not auto_rounds:
            opening = topic or "閹存垳婊戞稉鈧挧鐑芥娓氳儻浜伴懕濠傛儌閿涘奔缍樻禒顑胯⒈娑擃亙绡冮崚顐亰閺呭墽娼冮幋鎴欌偓?"
            self.handle_user_text(opening, prefetch=True)
        self.run_auto_turns(max(0, int(max_turns)), sleep_between=True)

    def run_interactive(self) -> None:
        while True:
            try:
                raw = self.input_fn(chr(10) + "[" + self.user_name + "] (enter=auto) > ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not raw:
                self.play_single_auto_turn()
                continue
            if raw in ("/exit", "/quit"):
                break
            if raw.startswith("/auto "):
                try:
                    rounds = int(raw.split("/auto ", 1)[1])
                except (ValueError, IndexError):
                    rounds = 3
                self.printer()
                self.printer("--- Auto " + str(rounds) + " rounds ---")
                self.prediction.clear()
                self.play_auto_cycle(rounds=rounds, prefetch=False)
                continue
            if raw.startswith("/watch"):
                parts = raw.split()
                try:
                    limit = int(parts[1]) if len(parts) > 1 else 0
                except ValueError:
                    limit = 0
                self.printer()
                self.printer("--- Watch mode " + ("unlimited" if limit <= 0 else str(limit) + " turns") + " ---")
                self.prediction.clear()
                self.run_auto_turns(limit, sleep_between=True)
                continue
            if raw == "/save":
                save_chat_logs(self.orchestrator, printer=self.printer)
                continue
            self.handle_user_text(raw, prefetch=False)


def create_dialogue_runtime(
    *,
    orchestrator,
    user_name: str,
    speech_gate: SpeechGate,
    prediction: MultiTurnPredictor,
    resources: MultiSpeechResources,
    watch_enabled: Callable[[], bool],
    idle_seconds: float,
    wait_for_engines: Callable[[dict[str, object]], None],
    printer: Callable[..., None] = print,
    input_fn: Callable[[str], str] = input,
) -> MultiDialogueRuntime:
    return MultiDialogueRuntime(
        orchestrator=orchestrator,
        user_name=user_name,
        speech_gate=speech_gate,
        prediction=prediction,
        resources=resources,
        watch_enabled=watch_enabled,
        idle_seconds=idle_seconds,
        wait_for_engines=wait_for_engines,
        printer=printer,
        input_fn=input_fn,
    )
