#!/usr/bin/env python3
"""Text-only CLI for persona testing and prompt iteration."""

from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime
from pathlib import Path

import requests

from kokoro import agent_loop
from kokoro import chat_session
from kokoro import config as cfg
from kokoro import llm_client
from kokoro import memory as mem_mod
from kokoro import text_cli_tools
from kokoro import token_usage


CONFIG = cfg.load()


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alice text-only test CLI")
    parser.add_argument("--character", "-c", default="alice", help="Character id (default: alice)")
    parser.add_argument("--model", default=None, help="Chat model")
    parser.add_argument("--no-memory", action="store_true", help="Disable memory backend for this run")
    parser.add_argument("--no-tools", action="store_true", help="Disable agent file tools")
    parser.add_argument("--read-only-tools", action="store_true", help="Enable file tools without write access")
    parser.add_argument("--max-history", type=int, default=40, help="Conversation history messages to keep")
    parser.add_argument("--no-store", action="store_true", help="Do not store turns into memory")
    parser.add_argument("--no-cognition", action="store_true", help="Disable cognition evaluation for this run")
    parser.add_argument("--input-file", default=None, help="Read UTF-8 user turns from a file")
    parser.add_argument("--transcript-file", default=None, help="Write a UTF-8 transcript to a file")
    return parser.parse_args()


def main() -> None:
    args = get_args()

    runtime_config = dict(CONFIG)
    if args.no_memory:
        runtime_config["memory_backend"] = "none"
    memory_backend = mem_mod.create_backend(runtime_config)

    try:
        session = chat_session.load_session(
            args.character,
            memory_backend,
            max_history=max(2, args.max_history),
            cognition_eval_interval=0 if args.no_cognition else None,
        )
    except KeyError:
        print(f"[error] Character '{args.character}' not found")
        return

    session.load_summary()
    model = args.model or session.character_config.get("llm_model") or cfg.llm_model()

    agent_config = None
    registry = None
    if not args.no_tools:
        registry = text_cli_tools.ProjectFileRegistry(allow_write=not args.read_only_tools)
        agent_config = agent_loop.AgentConfig(
            tools=registry.enabled_schemas(),
            tool_registry=registry,
            max_tool_iterations=8,
            tool_timeout=20.0,
        )

    print("=" * 50)
    print("  Alice Text CLI")
    print(f"  Character: {session.character_name}")
    print(f"  Model: {model}")
    print(f"  Memory: {not args.no_memory}")
    print(f"  Store turns: {not args.no_store}")
    print(f"  Cognition eval: {not args.no_cognition}")
    print(f"  File tools: {bool(agent_config)} (write={not args.read_only_tools and not args.no_tools})")
    print("  Commands: /exit, /usage")
    print("=" * 50)

    input_turns = None
    if args.input_file:
        input_turns = [
            line.strip()
            for line in Path(args.input_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    transcript_path = Path(args.transcript_file) if args.transcript_file else _default_transcript_path()
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript = transcript_path.open("w", encoding="utf-8", buffering=1)
    print(f"  Log file: {transcript_path}")

    stdin_is_pipe = input_turns is not None or not sys.stdin.isatty()
    repeated_error = ""
    repeated_error_count = 0

    transcript.write("# Alice Text CLI Log\n\n")
    transcript.write(f"- Character: {session.character_name}\n")
    transcript.write(f"- Model: {model}\n")
    transcript.write(f"- Memory: {not args.no_memory}\n")
    transcript.write(f"- Store turns: {not args.no_store}\n")
    transcript.write(f"- Cognition eval: {not args.no_cognition}\n")
    transcript.write(f"- File tools: {bool(agent_config)} (write={not args.read_only_tools and not args.no_tools})\n\n")

    try:
        turn_iter = iter(input_turns) if input_turns is not None else None
        while True:
            if turn_iter is not None:
                try:
                    user_text = next(turn_iter)
                except StopIteration:
                    break
                print("\nYou> ", end="", flush=True)
            else:
                try:
                    user_text = input("\nYou> ").strip()
                except EOFError:
                    break
            if not user_text:
                continue
            if stdin_is_pipe:
                print(user_text)
            if transcript:
                transcript.write(f"You: {user_text}\n")
            if user_text in {"/exit", "/quit"}:
                break
            if user_text == "/usage":
                usage = token_usage.summary()
                print(usage)
                transcript.write(f"```text\n{usage}\n```\n\n")
                continue

            messages = session.build_messages(
                user_text,
                include_screen=False,
                inject_memory=not args.no_memory,
            )

            print(f"\n{session.character_name}> ", end="", flush=True)
            try:
                result = agent_loop.agent_chat(
                    messages,
                    model,
                    agent_config=agent_config,
                    cancel_event=threading.Event(),
                    tts_engine=None,
                    character_config=session.character_config,
                    usage_callback=token_usage.make_callback(model, "text_cli_chat"),
                    session=session,
                    memory_backend=memory_backend,
                    character_id=session.character_id,
                )
            except requests.exceptions.ConnectionError:
                message = f"[connection failed] Cannot connect to {llm_client.api_base_for(model)}"
                print(f"\n{message}")
                transcript.write(f"{message}\n\n")
                if stdin_is_pipe:
                    break
                continue
            except KeyboardInterrupt:
                print("\n[interrupted]")
                transcript.write("[interrupted]\n\n")
                continue
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                print(f"\n[error] {message}")
                transcript.write(f"[error] {message}\n\n")
                if stdin_is_pipe:
                    if message == repeated_error:
                        repeated_error_count += 1
                    else:
                        repeated_error = message
                        repeated_error_count = 1
                    if repeated_error_count >= 3:
                        print("[batch stopped] Same error repeated 3 times.")
                        break
                continue

            reply = result.reply.strip()
            if transcript:
                transcript.write(f"{session.character_name}: {reply}\n\n")
            if reply and not args.no_store:
                session.remember(user_text, reply, async_store=True)

    except KeyboardInterrupt:
        print("\n[stopped]")
    finally:
        if registry is not None:
            registry.shutdown()
        close = getattr(memory_backend, "close", None)
        if callable(close):
            close()
        usage = token_usage.summary()
        transcript.write(f"```text\n{usage}\n```\n")
        transcript.close()
        print()
        print(usage)


def _default_transcript_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("logs") / f"text-cli-{stamp}.log"


if __name__ == "__main__":
    main()
