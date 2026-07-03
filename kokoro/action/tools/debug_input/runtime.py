"""Persistent plain-text debug input channel for local runtime testing."""

from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from kokoro.action import input_sources


@dataclass
class DebugInputRuntime:
    stop_event: threading.Event
    thread: threading.Thread | None
    inbox: Path
    outbox: Path

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)


def start(
    *,
    config: dict,
    root: Path,
    session,
    machine,
    handle_turn: Callable[[str], None],
    cancel_slot: list[threading.Event | None],
    dialogue=None,
    enabled_override: bool | None = None,
) -> DebugInputRuntime:
    section = config.get("debug_input", {})
    if not isinstance(section, dict):
        section = {}
    enabled = bool(section.get("enabled", False))
    if enabled_override is not None:
        enabled = bool(enabled_override)

    inbox = _resolve_path(root, str(section.get("inbox") or "data/debug/inbox.jsonl"))
    outbox = _resolve_path(root, str(section.get("outbox") or "data/debug/outbox.jsonl"))
    stop_event = threading.Event()

    if not enabled:
        return DebugInputRuntime(stop_event=stop_event, thread=None, inbox=inbox, outbox=outbox)

    inbox.parent.mkdir(parents=True, exist_ok=True)
    outbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.touch(exist_ok=True)
    outbox.touch(exist_ok=True)
    _write_outbox(outbox, {"type": "debug_ready", "inbox": str(inbox), "outbox": str(outbox)})
    _subscribe_event_tap(session, outbox)

    worker = threading.Thread(
        target=_watch_loop,
        kwargs={
            "inbox": inbox,
            "outbox": outbox,
            "session": session,
            "machine": machine,
            "handle_turn": handle_turn,
            "cancel_slot": cancel_slot,
            "dialogue": dialogue,
            "stop_event": stop_event,
            "poll_seconds": max(0.05, float(section.get("poll_seconds", 0.2))),
            "allow_interrupt": bool(section.get("allow_interrupt", True)),
            "default_mode": str(section.get("default_mode") or "turn").strip() or "turn",
        },
        daemon=True,
        name="debug-input",
    )
    worker.start()
    print(f"  [debug-input] enabled inbox={inbox} outbox={outbox}")
    return DebugInputRuntime(stop_event=stop_event, thread=worker, inbox=inbox, outbox=outbox)


def start_from_cli(
    *,
    args,
    config: dict,
    root: Path,
    session,
    machine,
    handle_turn: Callable[[str], None],
    cancel_slot: list[threading.Event | None],
    dialogue=None,
) -> DebugInputRuntime:
    enabled_override = True if getattr(args, "debug_input", False) else False if getattr(args, "no_debug_input", False) else None
    return start(
        config=config,
        root=root,
        session=session,
        machine=machine,
        handle_turn=handle_turn,
        cancel_slot=cancel_slot,
        dialogue=dialogue,
        enabled_override=enabled_override,
    )


def _subscribe_event_tap(session, outbox: Path) -> None:
    bus = getattr(session, "event_bus", None)
    if bus is None or not hasattr(bus, "subscribe"):
        return

    def on_event(event) -> None:
        try:
            _write_outbox(
                outbox,
                {
                    "type": "runtime_event",
                    "event_id": getattr(event, "id", ""),
                    "event_type": getattr(event, "type", ""),
                    "source": getattr(event, "source", ""),
                    "priority": getattr(event, "priority", ""),
                    "metadata": dict(getattr(event, "metadata", {}) or {}),
                    "content": event.visible_content()[:1200] if hasattr(event, "visible_content") else str(getattr(event, "content", ""))[:1200],
                },
            )
        except Exception:
            pass

    bus.subscribe(on_event)


def _watch_loop(
    *,
    inbox: Path,
    outbox: Path,
    session,
    machine,
    handle_turn: Callable[[str], None],
    cancel_slot: list[threading.Event | None],
    dialogue,
    stop_event: threading.Event,
    poll_seconds: float,
    allow_interrupt: bool,
    default_mode: str,
) -> None:
    offset = inbox.stat().st_size if inbox.exists() else 0
    seq = 0
    while not stop_event.is_set():
        try:
            if not inbox.exists():
                inbox.touch()
                offset = 0
            size = inbox.stat().st_size
            if size < offset:
                offset = 0
            if size > offset:
                with inbox.open("r", encoding="utf-8") as file:
                    file.seek(offset)
                    lines = file.readlines()
                    offset = file.tell()
                for line in lines:
                    raw = line.strip()
                    if raw:
                        seq += 1
                        _handle_line(
                            raw,
                            outbox=outbox,
                            session=session,
                            machine=machine,
                            handle_turn=handle_turn,
                            cancel_slot=cancel_slot,
                            dialogue=dialogue,
                            seq=seq,
                            allow_interrupt=allow_interrupt,
                            default_mode=default_mode,
                        )
            stop_event.wait(poll_seconds)
        except Exception as exc:
            _write_outbox(
                outbox,
                {
                    "type": "debug_error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=5),
                },
            )
            stop_event.wait(max(0.5, poll_seconds))


def _handle_line(
    raw: str,
    *,
    outbox: Path,
    session,
    machine,
    handle_turn: Callable[[str], None],
    cancel_slot: list[threading.Event | None],
    dialogue,
    seq: int,
    allow_interrupt: bool,
    default_mode: str,
) -> None:
    raw = raw.lstrip("\ufeff")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"mode": default_mode, "text": raw}
    if not isinstance(payload, dict):
        payload = {"mode": default_mode, "text": str(payload)}

    mode = str(payload.get("mode") or default_mode).strip().lower()
    text = str(payload.get("text") or payload.get("content") or "").strip()
    debug_id = str(payload.get("debug_id") or payload.get("id") or uuid.uuid4().hex[:12])
    interrupt = bool(payload.get("interrupt", mode == "turn")) and allow_interrupt

    if mode not in ("turn", "event", "command"):
        _write_outbox(outbox, {"type": "debug_rejected", "debug_id": debug_id, "reason": f"unknown mode: {mode}"})
        return
    if not text and mode != "command":
        _write_outbox(outbox, {"type": "debug_rejected", "debug_id": debug_id, "reason": "empty text"})
        return

    if mode == "command":
        _handle_command(
            text or str(payload.get("command") or ""),
            payload=payload,
            outbox=outbox,
            session=session,
            machine=machine,
            cancel_slot=cancel_slot,
            debug_id=debug_id,
        )
        return

    event = input_sources.publish_debug_text(session, text, mode=mode, debug_id=debug_id, seq=seq)
    _write_outbox(
        outbox,
        {
            "type": "debug_accepted",
            "debug_id": debug_id,
            "seq": seq,
            "mode": mode,
            "event_id": getattr(event, "id", ""),
            "priority": getattr(event, "priority", ""),
            "text": text,
        },
    )

    if mode == "event":
        return

    if interrupt:
        cancel = cancel_slot[0]
        if cancel:
            cancel.set()
    if dialogue is not None and hasattr(dialogue, "cancel_plans"):
        dialogue.cancel_plans()

    threading.Thread(target=handle_turn, args=(text,), daemon=True, name=f"debug-turn-{debug_id}").start()
    _write_outbox(outbox, {"type": "debug_turn_started", "debug_id": debug_id, "text": text})


def _handle_command(
    command: str,
    *,
    payload: dict,
    outbox: Path,
    session,
    machine,
    cancel_slot: list[threading.Event | None],
    debug_id: str,
) -> None:
    command = str(command or "").strip().lower()
    if command == "cancel":
        cancel = cancel_slot[0]
        if cancel:
            cancel.set()
        _write_outbox(outbox, {"type": "debug_command_result", "debug_id": debug_id, "command": "cancel", "cancelled": bool(cancel)})
        return
    if command == "state":
        bus = getattr(session, "event_bus", None)
        events = bus.snapshot(20) if bus is not None and hasattr(bus, "snapshot") else []
        _write_outbox(
            outbox,
            {
                "type": "debug_state",
                "debug_id": debug_id,
                "machine_state": getattr(getattr(machine, "state", None), "value", str(getattr(machine, "state", ""))),
                "recent_events": [
                    {
                        "id": event.id,
                        "type": event.type,
                        "source": event.source,
                        "priority": event.priority,
                        "content": event.visible_content()[:240],
                    }
                    for event in events
                ],
            },
        )
        return
    _write_outbox(outbox, {"type": "debug_command_result", "debug_id": debug_id, "command": command, "status": "unknown"})


def _write_outbox(path: Path, payload: dict) -> None:
    record = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(),
        **payload,
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path
