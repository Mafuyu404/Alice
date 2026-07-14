#!/usr/bin/env python3
"""Run a life-debug session with a simulated QQ private chat transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import websockets


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kokoro.core import console as console_mod


console_mod.ensure_utf8_console()


@dataclass(frozen=True)
class ScriptedMessage:
    delay: float
    text: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simulate a QQ private chat against the life debug runtime.")
    parser.add_argument("--character", default="lerwa")
    parser.add_argument("--duration-seconds", type=float, default=360.0)
    parser.add_argument("--port", type=int, default=58931)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--api-style", default="", choices=["", "auto", "ollama", "openai"])
    parser.add_argument("--sender-id", default="282170001")
    parser.add_argument("--sender-name", default="真冬")
    parser.add_argument(
        "--message",
        action="append",
        default=[],
        help="Timed external QQ message as seconds:text. Can be repeated.",
    )
    parser.add_argument("--startup-timeout", type=float, default=90.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir or ROOT / "test_runs" / f"simulated_qq_conversation_{args.character}_{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)
    messages = _messages_from_args(args.message)
    _write_json(out_dir / "scripted_messages.json", [message.__dict__ for message in messages])

    stdout_path = out_dir / "cli.out.log"
    stderr_path = out_dir / "cli.err.log"
    cmd = [
        sys.executable,
        "cli.py",
        "--life-debug",
        "--character",
        args.character,
        "--duration-seconds",
        str(max(args.duration_seconds + 30.0, args.duration_seconds)),
        "--debug-run",
        "--out-dir",
        str(out_dir),
        "--qq",
        "--qq-port",
        str(args.port),
        "--real-llm",
        "--initial-event",
        "QQ模拟对话测试开始：外部会像真实私聊一样逐句输入。",
    ]
    if str(args.model or "").strip():
        cmd.extend(["--llm-model", str(args.model).strip()])
    if str(args.api_style or "").strip():
        cmd.extend(["--api-style", str(args.api_style).strip()])
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    process = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=stdout_path.open("w", encoding="utf-8"),
        stderr=stderr_path.open("w", encoding="utf-8"),
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    (out_dir / "pid.txt").write_text(str(process.pid), encoding="utf-8")
    try:
        result = asyncio.run(
            _run_client(
                port=args.port,
                messages=messages,
                sender_id=str(args.sender_id),
                sender_name=str(args.sender_name),
                duration_seconds=float(args.duration_seconds),
                startup_timeout=float(args.startup_timeout),
                out_dir=out_dir,
            )
        )
    finally:
        _terminate(process)

    result["process_returncode"] = process.poll()
    result["run_dir"] = str(out_dir)
    _write_json(out_dir / "conversation_summary.json", result)
    print(str(out_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


async def _run_client(
    *,
    port: int,
    messages: list[ScriptedMessage],
    sender_id: str,
    sender_name: str,
    duration_seconds: float,
    startup_timeout: float,
    out_dir: Path,
) -> dict[str, Any]:
    uri = f"ws://127.0.0.1:{port}"
    ws = await _connect(uri, timeout=startup_timeout)
    start = time.monotonic()
    transcript: list[dict[str, Any]] = []
    sent_count = 0
    receive_task = asyncio.create_task(_receive_loop(ws, transcript))
    try:
        for index, message in enumerate(messages, 1):
            await _sleep_until(start + max(0.0, message.delay))
            event = _private_message_event(
                message_id=202607110000 + index,
                sender_id=sender_id,
                sender_name=sender_name,
                text=message.text,
            )
            await ws.send(json.dumps({"kind": "onebot_event", "event": event}, ensure_ascii=False))
            sent_count += 1
            transcript.append(
                {
                    "at": _now_iso(),
                    "elapsed": round(time.monotonic() - start, 3),
                    "direction": "external_to_ai",
                    "conversation_id": f"private:{sender_id}",
                    "text": message.text,
                }
            )
        await _sleep_until(start + max(duration_seconds, max((item.delay for item in messages), default=0.0)))
    finally:
        receive_task.cancel()
        try:
            await receive_task
        except asyncio.CancelledError:
            pass
        await ws.close()

    replies = [item for item in transcript if item.get("direction") == "ai_to_external"]
    _write_json(out_dir / "conversation_transcript.json", transcript)
    return {
        "external_message_count": sent_count,
        "reply_count": len(replies),
        "replies": replies,
        "transcript_path": str(out_dir / "conversation_transcript.json"),
    }


async def _connect(uri: str, *, timeout: float):
    deadline = time.monotonic() + max(1.0, timeout)
    last_error = ""
    while time.monotonic() < deadline:
        try:
            return await websockets.connect(uri)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(1.0)
    raise RuntimeError(f"simulated QQ transport could not connect to {uri}: {last_error}")


async def _receive_loop(ws, transcript: list[dict[str, Any]]) -> None:
    while True:
        raw = await ws.recv()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if payload.get("action") != "send_msg":
            continue
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        text = str(params.get("message") or "")
        message_type = str(params.get("message_type") or "")
        target = str(params.get("user_id") or params.get("group_id") or "")
        transcript.append(
            {
                "at": _now_iso(),
                "direction": "ai_to_external",
                "message_type": message_type,
                "target": target,
                "text": text,
                "raw": payload,
            }
        )


def _private_message_event(*, message_id: int, sender_id: str, sender_name: str, text: str) -> dict[str, Any]:
    return {
        "time": int(time.time()),
        "self_id": 3918005263,
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": message_id,
        "user_id": int(sender_id) if str(sender_id).isdigit() else sender_id,
        "message": text,
        "raw_message": text,
        "sender": {
            "user_id": int(sender_id) if str(sender_id).isdigit() else sender_id,
            "nickname": sender_name,
        },
    }


def _messages_from_args(raw_messages: list[str]) -> list[ScriptedMessage]:
    if raw_messages:
        parsed: list[ScriptedMessage] = []
        for raw in raw_messages:
            if ":" not in raw:
                raise ValueError(f"--message must be seconds:text, got {raw!r}")
            seconds_text, text = raw.split(":", 1)
            parsed.append(ScriptedMessage(delay=float(seconds_text.strip()), text=text.strip()))
        return sorted(parsed, key=lambda item: item.delay)
    return [
        ScriptedMessage(8.0, "雪吱，你现在研究 MC 冒险模组进度怎么样？"),
        ScriptedMessage(80.0, "你刚才说的进展里，最想继续钻哪一块？"),
        ScriptedMessage(170.0, "如果做成一个主线冒险模组，你觉得开局应该怎么吸引玩家？"),
        ScriptedMessage(280.0, "先别急着查资料，你自己现在最有感觉的设计方向是什么？"),
    ]


async def _sleep_until(target: float) -> None:
    while True:
        remaining = target - time.monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(min(1.0, remaining))


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
