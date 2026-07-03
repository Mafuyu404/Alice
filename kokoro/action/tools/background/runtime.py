"""Background tool loops used by entrypoints."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from kokoro.action.tools import observe_screen
from kokoro.core import memory_events


@dataclass
class BackgroundToolThreads:
    threads: list[threading.Thread] = field(default_factory=list)

    def add(self, target: Callable[[], None], *, name: str) -> threading.Thread:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self.threads.append(thread)
        return thread


@dataclass
class BackgroundToolRuntime:
    threads: list[threading.Thread] = field(default_factory=list)
    screen_watch_enabled: bool = False
    edge_cache_enabled: bool = False
    memory_events_enabled: bool = False


def screen_vision_timeout(config: dict) -> int:
    screen_cfg = config.get("screen_watch", {})
    if not isinstance(screen_cfg, dict):
        screen_cfg = {}
    return max(5, int(screen_cfg.get("vision_timeout", 45)))


def start_default_runtime(
    *,
    machine,
    config: dict,
    no_screen_watch: bool,
    memory_backend,
    session,
    tts_engine,
    dialogue,
    use_proactive: Callable[[], bool] | bool,
) -> BackgroundToolRuntime:
    screen_cfg = config.get("screen_watch", {})
    if not isinstance(screen_cfg, dict):
        screen_cfg = {}
    screen_watch_enabled = bool(screen_cfg.get("enabled", False)) and not no_screen_watch
    screen_watch_interval = max(10.0, float(screen_cfg.get("watch_interval", 45.0)))
    screen_interest_threshold = max(0.0, float(screen_cfg.get("interest_threshold", 70.0)))
    vision_timeout = screen_vision_timeout(config)
    edge_cache_config = observe_screen.edge_cache_config_from_dict(config)
    memory_detector = memory_events.from_config(config, memory_backend, session.character_id)

    runtime = BackgroundToolRuntime(
        screen_watch_enabled=screen_watch_enabled,
        edge_cache_enabled=edge_cache_config.enabled,
        memory_events_enabled=memory_detector.config.enabled,
    )
    runtime.threads.append(
        start_dialogue_context_worker(
            machine=machine,
            dialogue=dialogue,
            use_proactive=use_proactive,
        )
    )
    runtime.threads.append(
        start_screen_cache_worker(
            machine=machine,
            enabled=screen_watch_enabled,
            watch_interval=screen_watch_interval,
            interest_threshold=screen_interest_threshold,
            vision_timeout=vision_timeout,
        )
    )
    runtime.threads.append(
        start_edge_page_cache_worker(
            machine=machine,
            edge_cache_config=edge_cache_config,
        )
    )
    runtime.threads.append(
        start_memory_event_worker(
            machine=machine,
            memory_detector=memory_detector,
            session=session,
        )
    )
    runtime.threads.append(
        start_error_recovery_worker(
            machine=machine,
            tts_engine=tts_engine,
        )
    )
    return runtime


def start_screen_cache_worker(
    *,
    machine,
    enabled: bool,
    watch_interval: float,
    interest_threshold: float,
    vision_timeout: int,
) -> threading.Thread:
    def screen_cache_worker() -> None:
        sc = observe_screen.get_screen_interest_cache()
        while not machine.is_shutting_down:
            if not enabled:
                time.sleep(1.0)
                continue

            t0 = time.perf_counter()
            try:
                result = observe_screen.analyze_screen_interest(timeout=vision_timeout)
            except Exception as exc:
                print(f"\n[screen watch error] {type(exc).__name__}: {exc}")
                time.sleep(5.0)
                continue

            sc.put(result)
            if not machine.is_busy and result.score >= interest_threshold:
                context = result.content or result.reason
                print(f"\n  [screen] cached interest={result.score:.1f} {context.split(chr(10))[0]}")

            elapsed = time.perf_counter() - t0
            if elapsed < watch_interval:
                time.sleep(watch_interval - elapsed)

    thread = threading.Thread(target=screen_cache_worker, name="screen-cache-tool", daemon=True)
    thread.start()
    return thread


def start_edge_page_cache_worker(*, machine, edge_cache_config) -> threading.Thread:
    def edge_page_cache_worker() -> None:
        last_cache_signature = ""
        last_error_message = ""
        while not machine.is_shutting_down:
            if not edge_cache_config.enabled:
                time.sleep(1.0)
                continue

            t0 = time.perf_counter()
            try:
                payload = observe_screen.capture_edge_cache(edge_cache_config)
                last_error_message = ""
                title = payload.get("tab", {}).get("title") or "(untitled)"
                tab = payload.get("tab", {})
                signature = "|".join(
                    [
                        str(tab.get("url") or ""),
                        str(tab.get("title") or ""),
                        str(payload.get("text") or "")[:500],
                    ]
                )
                if signature != last_cache_signature:
                    last_cache_signature = signature
                    print(f"\n  [edge] cached page: {str(title)[:80]}")
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                observe_screen.write_edge_error_cache(edge_cache_config.cache_file, message)
                if message != last_error_message:
                    last_error_message = message
                    last_cache_signature = ""
                    print(f"\n[edge cache error] {message}")

            elapsed = time.perf_counter() - t0
            if elapsed < edge_cache_config.interval_seconds:
                time.sleep(edge_cache_config.interval_seconds - elapsed)

    thread = threading.Thread(target=edge_page_cache_worker, name="edge-cache-tool", daemon=True)
    thread.start()
    return thread


def start_memory_event_worker(*, machine, memory_detector, session) -> threading.Thread:
    def memory_event_worker() -> None:
        while not machine.is_shutting_down:
            time.sleep(memory_detector.config.check_interval)
            if not memory_detector.config.enabled:
                continue
            if machine.is_busy:
                continue
            for event in memory_detector.poll():
                session.add_screen_context(event.context)
                memory_detector.mark_emitted(event)
                print(f"\n  [memory] {event.source} interest={event.score:.1f}")

    thread = threading.Thread(target=memory_event_worker, name="memory-event-tool", daemon=True)
    thread.start()
    return thread


def start_error_recovery_worker(*, machine, tts_engine) -> threading.Thread:
    def error_recovery_worker() -> None:
        from kokoro.core import state_machine as sm

        while not machine.is_shutting_down:
            time.sleep(1.0)
            if machine.state == sm.SystemState.ERROR:
                if tts_engine:
                    try:
                        tts_engine.prepare()
                    except Exception:
                        pass
                machine.recover_from_error()
                machine.reset_error_count()

    thread = threading.Thread(target=error_recovery_worker, name="error-recovery-tool", daemon=True)
    thread.start()
    return thread


def start_dialogue_context_worker(
    *,
    machine,
    dialogue,
    use_proactive: Callable[[], bool] | bool,
) -> threading.Thread:
    """Idle context loop for proactive speaking decisions."""

    def enabled() -> bool:
        return bool(use_proactive() if callable(use_proactive) else use_proactive)

    def dialogue_context_worker() -> None:
        while not machine.is_shutting_down:
            time.sleep(dialogue.idle_context_interval_seconds)
            if not enabled():
                continue
            if not machine.can_start_conversation:
                continue
            event = dialogue.build_context_event(reason="idle_context")
            if not event.extra_context:
                continue
            decision = dialogue.decide(event)
            if decision.action in ("speak", "backchannel", "schedule"):
                if decision.context_use == "none":
                    continue
                if decision.action == "schedule":
                    dialogue.add_plan(decision, created_from="idle_context")
                else:
                    dialogue.add_plan(decision, created_from="idle_context_now")

    thread = threading.Thread(target=dialogue_context_worker, name="dialogue-context-tool", daemon=True)
    thread.start()
    return thread
