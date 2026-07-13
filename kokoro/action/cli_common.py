#!/usr/bin/env python3
"""Shared helpers for CLI runtimes.

CLI owns microphone/STT orchestration. Chat, memory, model routing, and TTS
helpers live in kokoro package modules so they can be shared.  State machine
(kokoro/state_machine.py) is the single source of truth for what the system is
doing. All workers consult it instead of ad-hoc flags."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from kokoro.core import console as console_mod
from kokoro.core import config as cfg
from kokoro.core import lifecycle_debug


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


CONFIG = cfg.load()
console_mod.ensure_utf8_console()


class _TeeStream:
    def __init__(self, console, logfile):
        self._console = console
        self._logfile = logfile
        self.encoding = getattr(console, "encoding", "utf-8")
        self.errors = getattr(console, "errors", "replace")

    def write(self, text: str) -> int:
        self._console.write(text)
        self._logfile.write(text)
        return len(text)

    def flush(self) -> None:
        self._console.flush()
        self._logfile.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._console, "isatty", lambda: False)())


def _install_cli_log() -> object:
    root = str(_PROJECT_ROOT)
    log_dir = os.path.join(root, "logs")
    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(log_dir, f"cli-{stamp}.log")
    logfile = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = _TeeStream(original_stdout, logfile)
    sys.stderr = _TeeStream(original_stderr, logfile)
    print(f"[cli] Log file: {path}")
    return logfile, original_stdout, original_stderr


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KokoroMemo voice CLI")
    parser.add_argument(
        "--output-mode",
        choices=["full", "text", "life-debug"],
        default="full",
        help="Runtime/output mode: full lifecycle, text-only, or LifeRuntime debug trace",
    )
    parser.add_argument("--text", action="store_true", help="Shortcut for --output-mode text")
    parser.add_argument("--life-debug", action="store_true", help="Shortcut for --output-mode life-debug")
    parser.add_argument("--character", "-c", default="alice", help="Character id (default: alice)")
    parser.add_argument("--device", type=int, default=None, help="Microphone device id")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices")
    parser.add_argument("--model", default=None, help="Chat model")
    parser.add_argument("--no-stt", action="store_true", help="Disable microphone/STT input")
    parser.add_argument("--no-tts", action="store_true", help="Disable speech output")
    parser.add_argument("--no-portrait", action="store_true", help="Disable portrait overlay")
    parser.add_argument("--no-proactive", action="store_true", help="Disable proactive dialogue")
    parser.add_argument("--no-screen-watch", action="store_true", help="Disable screen context watcher")
    parser.add_argument("--no-tools", action="store_true", help="Disable tool calling (use legacy regex commands)")
    parser.add_argument("--debug-input", action="store_true", help="Enable persistent local JSONL debug input")
    parser.add_argument("--no-debug-input", action="store_true", help="Disable persistent local JSONL debug input")
    parser.add_argument("--debug-run", action="store_true", help="Write verbose per-run debug logs split by thinking, memory, and tools")
    parser.add_argument("--debug-run-dir", default="", help="Directory for --debug-run output")
    parser.add_argument("--qq", action="store_true", help="Enable local QQ input WebSocket server for qq_client.py")
    parser.add_argument("--qq-host", default=None, help="Local QQ input server host")
    parser.add_argument("--qq-port", type=int, default=None, help="Local QQ input server port")
    parser.add_argument("--bilibili-room", type=int, default=None, help="Bilibili live room ID (overrides config)")
    parser.add_argument("--multi", default=None, help="Multi-character mode: comma-separated IDs, e.g. 'alice,penglai'")
    parser.add_argument("--auto", type=int, default=5, help="Auto rounds before interactive in --multi mode")
    parser.add_argument("--watch", action="store_true", help="In --multi mode, keep characters talking without user input")
    parser.add_argument("--idle-seconds", type=float, default=0.6, help="Seconds between unattended --multi turns")
    parser.add_argument("--max-turns", type=int, default=0, help="Maximum unattended --multi turns; 0 means unlimited")
    parser.add_argument("--topic", default=None, help="Opening topic for --multi watch/auto mode")
    # Text output mode.
    parser.add_argument("--no-memory", action="store_true", help="Text mode: disable memory backend for this run")
    parser.add_argument("--tools", action="store_true", help="Text mode: enable agent file tools")
    parser.add_argument("--read-only-tools", action="store_true", help="Text mode: enable file tools without write access")
    parser.add_argument("--max-history", type=int, default=40, help="Text mode: conversation history messages to keep")
    parser.add_argument("--no-store", action="store_true", help="Text mode: do not store turns into memory")
    parser.add_argument("--no-cognition", action="store_true", help="Text mode: disable cognition evaluation for this run")
    parser.add_argument("--input-file", default=None, help="Text mode: read UTF-8 user turns from a file")
    parser.add_argument("--transcript-file", default=None, help="Text mode: write a UTF-8 transcript to a file")
    # LifeRuntime debug output mode.
    parser.add_argument("--ticks", type=int, default=3, help="Life debug mode: number of manual ticks")
    parser.add_argument("--duration-seconds", type=float, default=0.0, help="Life debug mode: run background loop for this many seconds")
    parser.add_argument("--out-dir", default="", help="Life debug mode: output directory")
    parser.add_argument("--real-llm", action="store_true", help="Life debug mode: use configured LLM instead of scripted responses")
    parser.add_argument("--llm-model", default="", help="Life debug mode: local thinking model override")
    parser.add_argument("--llm-url", default="", help="Life debug mode: local thinking base URL override")
    parser.add_argument("--api-style", default="", choices=["", "auto", "ollama", "openai"], help="Life debug mode: local thinking API style")
    parser.add_argument(
        "--initial-event",
        default="LifeRuntime debug start: absorb this event, notice elapsed time, and decide whether a tool helps.",
        help="Life debug mode: initial event",
    )
    parser.add_argument("--guide-event", action="append", default=[], help="Life debug mode: timed guide event as seconds:text")
    parser.add_argument("--memory-event", action="append", default=[], help="Life debug mode: timed memory candidate as seconds:text")
    parser.add_argument("--real-memory", action="store_true", help="Life debug mode: use configured memory backend")
    args = parser.parse_args()
    if args.text:
        args.output_mode = "text"
    if args.life_debug:
        args.output_mode = "life-debug"
    return args


def configure_debug_run(args: argparse.Namespace, config: dict) -> Path | None:
    if not bool(getattr(args, "debug_run", False)):
        life_section = config.get("life_runtime", {}) if isinstance(config, dict) else {}
        debug_section = life_section.get("debug_cli", {}) if isinstance(life_section, dict) else {}
        if isinstance(debug_section, dict) and getattr(args, "output_mode", "") == "life-debug":
            args.debug_run = bool(debug_section.get("debug_run", False))
            if not str(getattr(args, "debug_run_dir", "") or "").strip():
                args.debug_run_dir = str(debug_section.get("debug_run_dir") or "")
    if not bool(getattr(args, "debug_run", False)):
        return None
    character = str(getattr(args, "character", "") or "default")
    target = str(getattr(args, "debug_run_dir", "") or "").strip()
    if not target:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = str(_PROJECT_ROOT / "debug_runs" / f"{character}_{stamp}")
    run_dir = lifecycle_debug.configure_run(
        target,
        metadata={
            "character": character,
            "output_mode": getattr(args, "output_mode", "full"),
            "argv": sys.argv,
        },
    )
    life_section = config.setdefault("life_runtime", {})
    if isinstance(life_section, dict):
        life_section["debug"] = True
        life_section["prompt_trace_dir"] = str(run_dir / "prompts")
    print(f"[debug-run] Run directory: {run_dir}")
    return run_dir


def display_user(text: str) -> None:
    sys.stdout.write(f"\r\033[K[User] {text}\n")
    sys.stdout.flush()


