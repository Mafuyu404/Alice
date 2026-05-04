#!/usr/bin/env python3
"""Voice-first CLI entrypoint.

CLI owns microphone/STT orchestration. Chat, memory, model routing, and TTS
helpers live in kokoro package modules so webui.py can share the same core.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
import threading

import requests

from kokoro import chat_session
from kokoro import config as cfg
from kokoro import llm_client
from kokoro import memory_events
from kokoro import memory as mem_mod
from kokoro import portrait_controller
from kokoro import pool as pool_mod
from kokoro import prompts
from kokoro import proactive
from kokoro import screen_interest
from kokoro import stt as stt_mod
from kokoro import tts as tts_mod
from kokoro import user_commands


CONFIG = cfg.load()


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KokoroMemo voice CLI")
    parser.add_argument("--character", default="alice", help="Character id")
    parser.add_argument("--device", type=int, default=None, help="Microphone device id")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices")
    parser.add_argument("--model", default=None, help="Chat model")
    parser.add_argument("--no-tts", action="store_true", help="Disable speech output")
    parser.add_argument("--no-portrait", action="store_true", help="Disable portrait overlay")
    parser.add_argument("--no-proactive", action="store_true", help="Disable proactive speech scheduler")
    parser.add_argument("--no-screen-watch", action="store_true", help="Disable proactive screen interest events")
    return parser.parse_args()


def display_user(text: str) -> None:
    sys.stdout.write(f"\r\033[K[User] {text}\n")
    sys.stdout.flush()


def chat_stream(
    messages: list[dict],
    char_name: str,
    model: str,
    tts_engine: object | None,
) -> str:
    print(f"\n{char_name}: ", end="", flush=True)
    reply = ""

    for content in llm_client.stream_chat(messages, model):
        print(content, end="", flush=True)
        reply += content
        if tts_engine:
            tts_engine.push(content)

    print()
    if reply and tts_engine:
        tts_engine.end_sentence()
    return reply


def create_tts_engine(enabled: bool):
    if not enabled:
        return None
    try:
        tts_mod.warmup()
        engine = tts_mod.StreamingTTS()
        engine.prepare()
        return engine
    except Exception as exc:
        print(f"  [cli] TTS init failed: {exc}")
        return None


def refine_endpoint() -> tuple[str, str, str | None]:
    model = cfg.stt_refine_model()
    if cfg.is_deepseek_model(model):
        return cfg.deepseek_url(), model, cfg.deepseek_api_key()
    return cfg.llm_url(), model, None


def main() -> None:
    args = get_args()

    if args.list_devices:
        stt_mod.list_devices()
        return

    memory_backend = mem_mod.create_backend(CONFIG)
    try:
        session = chat_session.load_session(args.character, memory_backend)
    except KeyError:
        from kokoro import character

        print(f"[error] Character '{args.character}' not found")
        print(f"Available characters: {', '.join(character.load().keys())}")
        return

    model = args.model or cfg.llm_model()
    tts_engine = create_tts_engine(not args.no_tts)
    portrait_client = None
    portrait_worker = None
    if not args.no_portrait:
        try:
            portrait_client, portrait_worker = portrait_controller.create_controller(model)
        except Exception as exc:
            print(f"  [cli] Portrait overlay init failed: {exc}")

    scheduler = proactive.from_config(CONFIG)
    if args.no_proactive:
        scheduler.config.enabled = False
    proactive_config = CONFIG.get("proactive", {})
    if not isinstance(proactive_config, dict):
        proactive_config = {}
    screen_cfg = CONFIG.get("screen_watch", {})
    if not isinstance(screen_cfg, dict):
        screen_cfg = {}
    screen_watch_enabled = bool(screen_cfg.get("enabled", False))
    if args.no_screen_watch or not scheduler.config.enabled:
        screen_watch_enabled = False
    screen_watch_interval = max(10.0, float(screen_cfg.get("watch_interval", 45.0)))
    screen_interest_threshold = max(0.0, float(screen_cfg.get("interest_threshold", 70.0)))
    screen_vision_timeout = max(5, int(screen_cfg.get("vision_timeout", 45)))
    memory_detector = memory_events.from_config(CONFIG, memory_backend, session.character_id)
    chat_lock = threading.Lock()
    screen_watch_state_lock = threading.Lock()
    screen_watch_seq = 0
    active_screen_watch_id = 0
    canceled_screen_watch_ids: set[int] = set()

    def begin_screen_watch() -> int:
        nonlocal screen_watch_seq, active_screen_watch_id
        with screen_watch_state_lock:
            screen_watch_seq += 1
            active_screen_watch_id = screen_watch_seq
            return active_screen_watch_id

    def cancel_active_screen_watch() -> bool:
        with screen_watch_state_lock:
            if active_screen_watch_id:
                canceled_screen_watch_ids.add(active_screen_watch_id)
                return True
            return False

    def consume_screen_watch_canceled(watch_id: int) -> bool:
        with screen_watch_state_lock:
            if watch_id in canceled_screen_watch_ids:
                canceled_screen_watch_ids.remove(watch_id)
                return True
            return False

    def finish_screen_watch(watch_id: int) -> None:
        nonlocal active_screen_watch_id
        with screen_watch_state_lock:
            if active_screen_watch_id == watch_id:
                active_screen_watch_id = 0

    def on_refined(text: str) -> None:
        scheduler.record_user_activity()
        scheduler.reset_all()
        display_user(text)
        with chat_lock:
            command_context = ""
            command = user_commands.detect(text)
            if command:
                if cancel_active_screen_watch():
                    scheduler.desires[proactive.Behavior.SCREEN] = 0.0
                    scheduler.screen_context = ""
                    print("\n  [screen] active watch canceled by command")
                try:
                    waiting_reply = user_commands.build_waiting_reply(
                        text,
                        session.history,
                        llm_url=refine_url,
                        llm_model=refine_model,
                        api_key=refine_key,
                    )
                except Exception:
                    waiting_reply = "好，我看一下。"

                print(f"\n{session.character_name}: {waiting_reply}")
                if tts_engine:
                    tts_engine.push(waiting_reply)
                    tts_engine.end_sentence()
                    while tts_engine.is_playing:
                        time.sleep(0.1)

                result = user_commands.execute(command, timeout=screen_vision_timeout)
                command_context = result.context
                if result.ok and result.screen_context:
                    scheduler.desires[proactive.Behavior.SCREEN] = 0.0
                    scheduler.screen_context = ""
                    session.add_screen_context(result.screen_context)
                    print(f"\n  [screen] command interest={result.score:.1f} {result.screen_context}")
                elif result.user_visible_note:
                    label = "private" if result.private else "error"
                    print(f"\n  [screen] command {label}: {result.user_visible_note}")
                    command_context = result.context or result.user_visible_note

            messages = session.build_messages(text, extra_context=command_context)

            try:
                reply = chat_stream(messages, session.character_name, model, tts_engine)
            except requests.exceptions.ConnectionError:
                print(f"\n[connection failed] Cannot connect to {llm_client.api_base_for(model)}")
                return
            except Exception as exc:
                print(f"\n[error] {type(exc).__name__}: {exc}")
                return

            session.remember(text, reply, async_store=True)
            scheduler.record_conversation_end(text, reply)
            if portrait_worker:
                portrait_worker.submit(text, reply)

            if tts_engine:
                while tts_engine.is_playing:
                    time.sleep(0.1)
                scheduler.record_tts_end()
                tts_engine.prepare()

    refine_url, refine_model, refine_key = refine_endpoint()
    pool = pool_mod.ConversationPool(
        llm_url=refine_url,
        llm_model=refine_model,
        on_refined=on_refined,
        api_key=refine_key,
    )

    device = args.device if args.device is not None else stt_mod.find_input_device()
    if device is None:
        print("\n[error] No microphone device found.")
        print("Run `python cli.py --list-devices` to inspect available devices.\n")
        pool.stop()
        return

    model_path = stt_mod.download_model(CONFIG.get("stt_model_dir", "models/stt"))
    print("  [cli] Loading speech model...")
    recognizer = stt_mod.create_recognizer(
        model_path,
        argparse.Namespace(num_threads=4, hotwords="", hotwords_score=1.5, verbose=False),
    )
    stt_stream = recognizer.create_stream()

    print()
    print("=" * 50)
    print("  Alice CLI")
    print(f"  Character: {session.character_name}")
    print(f"  Model: {model}")
    print(f"  Microphone: [{device}]")
    print(f"  TTS: {tts_engine is not None}")
    print(f"  Portrait: {portrait_worker is not None}")
    print(f"  Proactive: {scheduler.config.enabled}")
    print(f"  Screen watch: {screen_watch_enabled}")
    print(f"  Memory events: {memory_detector.config.enabled}")
    print("  Ctrl+C to stop")
    print("=" * 50)

    greeting = session.character_data.get("greeting")
    if greeting:
        print(f"\n{session.character_name}: {greeting}")

    stt_running = True
    last_partial = ""
    pause_during_tts = cfg.stt_pause_during_tts()

    def stt_worker() -> None:
        nonlocal last_partial, stt_stream
        import sounddevice as sd

        tts_was_playing = False
        audio_stream = None
        try:
            audio_stream = sd.InputStream(
                device=device,
                channels=1,
                samplerate=stt_mod.SAMPLE_RATE,
                dtype="float32",
                blocksize=1600,
            )
            audio_stream.start()

            while stt_running:
                chunk, _ = audio_stream.read(1600)

                if pause_during_tts and tts_engine and tts_engine.is_playing:
                    tts_was_playing = True
                    continue

                if tts_was_playing:
                    tts_was_playing = False
                    stt_stream = recognizer.create_stream()
                    last_partial = ""
                    continue

                mono = stt_mod.denoise(chunk[:, 0])
                stt_stream.accept_waveform(stt_mod.SAMPLE_RATE, mono)

                if recognizer.is_ready(stt_stream):
                    recognizer.decode_stream(stt_stream)
                    text = recognizer.get_result(stt_stream)
                    if text:
                        pool.add_chunk(text)
                        if text != last_partial:
                            sys.stdout.write(f"\r\033[K  [STT] {text}")
                            sys.stdout.flush()
                            last_partial = text
        except Exception as exc:
            print(f"\n[STT error] {exc}")
            traceback.print_exc()
        finally:
            if audio_stream is not None:
                try:
                    audio_stream.stop()
                    audio_stream.close()
                except Exception:
                    pass

    stt_thread = threading.Thread(target=stt_worker, daemon=True)
    stt_thread.start()

    def send_snapshot() -> None:
        if portrait_client:
            portrait_client.send_debug(scheduler.snapshot())

    def proactive_worker() -> None:
        while stt_running:
            time.sleep(scheduler.config.tick_seconds)
            send_snapshot()
            if not scheduler.config.enabled:
                continue

            busy = (tts_engine and tts_engine.is_playing) or chat_lock.locked()
            decision = scheduler.tick(busy=busy)
            if decision is None:
                continue

            with chat_lock:
                messages = session.build_messages(decision.prompt)
                guidance = session.character_data.get("proactive_guidance", "")
                sys_content = prompts.get("proactive.trigger_system", "")
                if guidance:
                    sys_content += prompts.format_prompt("proactive.trigger_guidance_label", guidance=guidance)
                messages.insert(
                    1,
                    {
                        "role": "system",
                        "content": sys_content,
                    }
                )
                print(
                    f"\n  [proactive] {decision.behavior.value} "
                    f"desire={decision.desire:.1f} disturb={decision.disturbance:.1f}"
                )
                try:
                    reply = chat_stream(messages, session.character_name, model, tts_engine)
                except requests.exceptions.ConnectionError:
                    print(f"\n[connection failed] Cannot connect to {llm_client.api_base_for(model)}")
                    continue
                except Exception as exc:
                    print(f"\n[proactive error] {type(exc).__name__}: {exc}")
                    continue

                if reply:
                    session.history.append({"role": "assistant", "content": reply})
                    if len(session.history) > session.max_history * 2:
                        session.history[:] = session.history[-session.max_history * 2 :]
                    if portrait_worker:
                        portrait_worker.submit("", reply)

                if tts_engine:
                    while tts_engine.is_playing and stt_running:
                        time.sleep(0.1)
                    scheduler.record_tts_end()
                    tts_engine.prepare()

    proactive_thread = threading.Thread(target=proactive_worker, daemon=True)
    proactive_thread.start()

    def screen_watch_worker() -> None:
        while stt_running:
            time.sleep(screen_watch_interval)
            if not screen_watch_enabled:
                continue
            if chat_lock.locked():
                continue
            if tts_engine and tts_engine.is_playing:
                continue
            watch_id = begin_screen_watch()
            try:
                result = screen_interest.analyze(timeout=screen_vision_timeout)
            except Exception as exc:
                if not consume_screen_watch_canceled(watch_id):
                    print(f"\n[screen watch error] {type(exc).__name__}: {exc}")
                finish_screen_watch(watch_id)
                continue
            finally:
                finish_screen_watch(watch_id)

            if consume_screen_watch_canceled(watch_id):
                continue

            if result.private:
                scheduler.quiet_until = max(scheduler.quiet_until, time.monotonic() + screen_watch_interval)
                continue
            if result.score >= screen_interest_threshold:
                context = result.content or result.reason
                scheduler.add_screen_interest(result.score, context)
                session.add_screen_context(context)
                print(f"\n  [screen] interest={result.score:.1f} {context}")

    screen_thread = threading.Thread(target=screen_watch_worker, daemon=True)
    screen_thread.start()

    def memory_event_worker() -> None:
        while stt_running:
            time.sleep(memory_detector.config.check_interval)
            if not scheduler.config.enabled or not memory_detector.config.enabled:
                continue
            if chat_lock.locked():
                continue
            if tts_engine and tts_engine.is_playing:
                continue
            for event in memory_detector.poll():
                scheduler.add_memory_interest(event.score, event.context)
                memory_detector.mark_emitted(event)
                print(f"\n  [memory] {event.source} interest={event.score:.1f}")

    memory_thread = threading.Thread(target=memory_event_worker, daemon=True)
    memory_thread.start()

    try:
        while stt_running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n\n[cli] Stopping...")
    finally:
        stt_running = False
        pool.stop()
        if portrait_worker:
            portrait_worker.stop()
        if portrait_client:
            portrait_client.shutdown()
        if tts_engine:
            tts_engine.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nBye.")
    except Exception as exc:
        print(f"\n[error] {type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        time.sleep(0.3)
