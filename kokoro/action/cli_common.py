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
    logfile = open(path, "a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = _TeeStream(original_stdout, logfile)
    sys.stderr = _TeeStream(original_stderr, logfile)
    print(f"[cli] Log file: {path}")
    return logfile, original_stdout, original_stderr


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KokoroMemo voice CLI")
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
    return parser.parse_args()


def display_user(text: str) -> None:
    sys.stdout.write(f"\r\033[K[User] {text}\n")
    sys.stdout.flush()


