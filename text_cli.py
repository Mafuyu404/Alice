#!/usr/bin/env python3
"""Text-only CLI for persona testing and prompt iteration."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

from kokoro.action import agent_loop
from kokoro.core import chat_session
from kokoro.core import console as console_mod
from kokoro.core import config as cfg
from kokoro.action import dialogue_orchestrator as dialogue_mod
from kokoro.core import llm_client
from kokoro.core import memory as mem_mod
from kokoro.action import multi_chat
from kokoro.core import token_usage
from kokoro.action.tools.observe_screen import edge_cache as edge_cache_mod
from kokoro.action.tools.task import manager as task_manager_mod
from kokoro.action.tools.text_cli import file_tools as text_cli_tools


CONFIG = cfg.load()
console_mod.ensure_utf8_console()


class _NullLayer:
    def get_context(self) -> str:
        return ""

    def refresh_cache(self, *args, **kwargs) -> None:
        return

    def evaluate(self, *args, **kwargs) -> None:
        return


def get_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alice text-only test CLI")
    parser.add_argument("--character", "-c", default="alice", help="Character id (default: alice)")
    parser.add_argument("--model", default=None, help="Chat model")
    parser.add_argument("--multi", default=None, help="Multi-character mode: comma-separated IDs, e.g. 'alice,penglai'")
    parser.add_argument("--auto", type=int, default=3, help="Auto follow-up turns after each user turn in multi-character mode")
    parser.add_argument("--no-memory", action="store_true", help="Disable memory backend for this run")
    parser.add_argument("--tools", action="store_true", help="Enable agent file tools")
    parser.add_argument("--read-only-tools", action="store_true", help="Enable file tools without write access")
    parser.add_argument("--max-history", type=int, default=40, help="Conversation history messages to keep")
    parser.add_argument("--no-store", action="store_true", help="Do not store turns into memory")
    parser.add_argument("--no-cognition", action="store_true", help="Disable cognition evaluation for this run")
    parser.add_argument("--input-file", default=None, help="Read UTF-8 user turns from a file")
    parser.add_argument("--transcript-file", default=None, help="Write a UTF-8 transcript to a file")
    return parser.parse_args(argv)


def main(args: argparse.Namespace | None = None) -> None:
    if args is None:
        args = get_args()

    # Multi-character mode
    if args.multi:
        _run_multi(args)
        return

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
    if args.no_memory:
        session.summary = ""
        session.cognition = _NullLayer()
        session.emotion = _NullLayer()
        session.inner_stream = _NullLayer()
        session.memory_events = None
    model = args.model or session.character_config.get("llm_model") or cfg.llm_model()
    dialogue = dialogue_mod.DialogueOrchestrator(
        config=runtime_config,
        session=session,
        model=model,
        memory_backend=memory_backend,
    )

    agent_config = None
    registry = None
    _task_manager = task_manager_mod.TaskManager()
    tools_enabled = bool(args.tools or args.read_only_tools)
    if tools_enabled:
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
    print(f"  File tools: {bool(agent_config)} (write={bool(args.tools and not args.read_only_tools)})")
    print("  Commands: /exit, /usage, /save")
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
    transcript = transcript_path.open("w", encoding="utf-8", errors="replace", buffering=1)
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
    transcript.write(f"- File tools: {bool(agent_config)} (write={bool(args.tools and not args.read_only_tools)})\n\n")

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
            user_text = _clean_cli_text(user_text)
            if not user_text:
                continue
            if stdin_is_pipe:
                print(user_text)
            if transcript:
                transcript.write(f"You: {user_text}\n")
            if user_text in {"/exit", "/quit"}:
                break
            if user_text == "/save":
                path = session.write_chat_log_to_file()
                if path:
                    print(f"\n[chat log saved] {path}")
                    transcript.write(f"[chat log saved] {path}\n\n")
                else:
                    print("\n[chat log save failed]")
                    transcript.write("[chat log save failed]\n\n")
                continue
            if user_text == "/usage":
                usage = token_usage.summary()
                print(usage)
                transcript.write(f"```text\n{usage}\n```\n\n")
                continue

            decision = dialogue.decide(dialogue_mod.DialogueEvent(
                type="user_utterance",
                text=user_text,
                source="user",
            ))

            if decision.action in ("silence", "observe", "cancel_plan"):
                if decision.action == "cancel_plan":
                    dialogue.cancel_plans()
                dialogue.record_user_observation(user_text, decision)
                print(f"\n{session.character_name}> [no reply]")
                transcript.write(f"{session.character_name}: [no reply]\n\n")
                continue

            if decision.action == "schedule":
                dialogue.record_user_observation(user_text, decision)
                dialogue.add_plan(decision, created_from=user_text)
                print(f"\n{session.character_name}> [scheduled]")
                transcript.write(f"{session.character_name}: [scheduled]\n\n")
                continue

            history_window = 30 if ("总结" in user_text or "summary" in user_text.lower()) else None
            messages = dialogue.build_reply_messages(
                user_text=user_text,
                decision=decision,
                max_history_messages=history_window,
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
                    task_manager=_task_manager,
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
            reply = dialogue_mod.clean_generated_reply(reply, session.character_name)
            if transcript:
                transcript.write(f"{session.character_name}: {reply}\n\n")
            if reply and not args.no_store:
                session.remember(user_text, reply, async_store=True)

    except KeyboardInterrupt:
        print("\n[stopped]")
    finally:
        if registry is not None:
            registry.shutdown()
        # Flush cached memory events to vector store
        if session is not None and hasattr(session, 'memory_events') and session.memory_events is not None:
            session.memory_events.flush_all(
                user_name=session.user_name,
                character_name=session.character_name,
                summary=session.summary or "",
            )
        close = getattr(memory_backend, "close", None)
        if callable(close):
            close()
        usage = token_usage.summary()
        transcript.write(f"```text\n{usage}\n```\n")
        transcript.close()
        print()
        print(usage)


def _run_multi(args: argparse.Namespace) -> None:
    """Multi-character dialogue mode."""
    cids = [c.strip() for c in args.multi.split(",") if c.strip()]
    if len(cids) < 2:
        print("[error] --multi needs at least 2 character IDs")
        return

    runtime_config = dict(CONFIG)
    if args.no_memory:
        runtime_config["memory_backend"] = "none"
    cfg_inst = multi_chat.MultiChatConfig(
        character_ids=cids,
        max_history=args.max_history,
        model=args.model or "",
        max_auto_followups=max(0, args.auto),
    )
    orch = multi_chat.MultiChatOrchestrator(cfg_inst, runtime_config=runtime_config)
    stop_event = threading.Event()
    edge_thread = _start_edge_cache_worker(runtime_config, stop_event)

    user_name = orch.user_name
    names = orch.character_names

    print("=" * 50)
    print("  Multi-Character Dialogue")
    for cid, cname in names.items():
        print(f"  {cid} → {cname}")
    print(f"  User: {user_name}")
    print(f"  Planner: {orch.planning_model}")
    print(f"  Memory: {not args.no_memory}")
    print(f"  Edge page cache: {edge_thread is not None}")
    print(f"  Auto followups: {max(0, args.auto)}")
    print(f"  Commands: /exit, /auto N, /history, /usage")
    print("  Empty input = execute due/idle multi-dialogue turn")
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
    transcript = transcript_path.open("w", encoding="utf-8", errors="replace", buffering=1)
    transcript.write("# Multi-Character Dialogue Log\n\n")
    transcript.write(f"- Characters: {', '.join(names.values())}\n")
    transcript.write(f"- Planner: {orch.planning_model}\n")
    transcript.write(f"- Memory: {not args.no_memory}\n\n")
    print(f"  Log file: {transcript_path}")

    try:
        turn_iter = iter(input_turns) if input_turns is not None else None
        stdin_is_pipe = input_turns is not None or not sys.stdin.isatty()
        while True:
            if turn_iter is not None:
                try:
                    raw = next(turn_iter)
                except StopIteration:
                    break
                print(f"\n[{user_name}] > ", end="", flush=True)
            else:
                try:
                    raw = input(f"\n[{user_name}] (enter=auto) > ").strip()
                except EOFError:
                    break
                except KeyboardInterrupt:
                    print("\n[stopped]")
                    break

            raw = _clean_cli_text(raw)
            if stdin_is_pipe and raw:
                print(raw)

            if not raw:
                cid, cname, reply = orch.auto_turn()
                if reply:
                    print(f"\n{cname}> {reply}")
                    transcript.write(f"{cname}: {reply}\n\n")
                else:
                    print("[no reply]")
                    transcript.write("[no reply]\n\n")
                continue

            if raw in ("/exit", "/quit"):
                break

            if raw.startswith("/auto "):
                try:
                    n = int(raw.split("/auto ", 1)[1])
                except (ValueError, IndexError):
                    n = 3
                print(f"\n--- Auto {n} rounds ---")
                for cid, cname, reply in orch.auto_cycle(rounds=n):
                    if reply:
                        print(f"{cname}> {reply}")
                        transcript.write(f"{cname}: {reply}\n\n")
                continue

            if raw == "/history":
                for entry in orch.shared_history:
                    print(f"  {entry.speaker}：{entry.text[:100]}")
                continue

            if raw == "/usage":
                usage = token_usage.summary()
                print(usage)
                transcript.write(f"```text\n{usage}\n```\n\n")
                continue

            if raw == "/save":
                for cid, session_obj in orch.sessions.items():
                    path = session_obj.write_chat_log_to_file()
                    if path:
                        print(f"[chat log saved] {path}")
                        transcript.write(f"[chat log saved] {path}\n\n")
                continue

            transcript.write(f"{user_name}: {raw}\n")
            try:
                turns = orch.user_turn(raw)
            except requests.exceptions.ConnectionError:
                message = f"[connection failed] Cannot connect to planner/model endpoint"
                print(message)
                transcript.write(f"{message}\n\n")
                if stdin_is_pipe:
                    break
                continue
            except Exception as exc:
                message = f"[error] {type(exc).__name__}: {exc}"
                print(message)
                transcript.write(f"{message}\n\n")
                if stdin_is_pipe:
                    break
                continue
            if not turns:
                print("[no reply]")
                transcript.write("[no reply]\n\n")
            for _cid, cname, reply in turns:
                print(f"\n{cname}> {reply}")
                transcript.write(f"{cname}: {reply}\n")
            transcript.write("\n")
    finally:
        stop_event.set()
        if edge_thread is not None:
            edge_thread.join(timeout=2.0)
        usage = token_usage.summary()
        transcript.write(f"```text\n{usage}\n```\n")
        transcript.close()
        orch.close()
        print()
        print(usage)


def _start_edge_cache_worker(runtime_config: dict, stop_event: threading.Event) -> threading.Thread | None:
    edge_cache_config = edge_cache_mod.config_from_dict(runtime_config)
    if not edge_cache_config.enabled:
        return None

    def worker() -> None:
        last_cache_signature = ""
        last_error_message = ""
        while not stop_event.is_set():
            t0 = time.perf_counter()
            try:
                payload = edge_cache_mod.capture_and_save(edge_cache_config)
                last_error_message = ""
                tab = payload.get("tab", {}) if isinstance(payload, dict) else {}
                title = tab.get("title") or "(untitled)"
                signature = "|".join([
                    str(tab.get("url") or ""),
                    str(tab.get("title") or ""),
                    str(payload.get("text") or "")[:500],
                ])
                if signature != last_cache_signature:
                    last_cache_signature = signature
                    print(f"\n  [edge] cached page: {str(title)[:80]}")
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                edge_cache_mod.write_error_cache(edge_cache_config.cache_file, message)
                if message != last_error_message:
                    last_error_message = message
                    last_cache_signature = ""
                    print(f"\n[edge cache error] {message}")

            elapsed = time.perf_counter() - t0
            wait_seconds = max(0.1, edge_cache_config.interval_seconds - elapsed)
            stop_event.wait(wait_seconds)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _default_transcript_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("logs") / f"text-cli-{stamp}.log"


def _clean_cli_text(text: str) -> str:
    cleaned = str(text or "").replace("\ufeff", "")
    return "".join(ch for ch in cleaned if not 0xD800 <= ord(ch) <= 0xDFFF).strip()


if __name__ == "__main__":
    import subprocess

    raise SystemExit(subprocess.call([sys.executable, "cli.py", "--output-mode", "text", *sys.argv[1:]]))
