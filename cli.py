#!/usr/bin/env python3
"""Voice-first CLI entrypoint.

CLI owns microphone/STT orchestration. Chat, memory, model routing, and TTS
helpers live in kokoro package modules so they can be shared.  State machine
(kokoro/state_machine.py) is the single source of truth for what the system is
doing. All workers consult it instead of ad-hoc flags."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import traceback
import threading
from datetime import datetime

import requests

from kokoro import chat_session
from kokoro import config as cfg
from kokoro import llm_client
from kokoro import prompts
from kokoro import memory_events
from kokoro import memory as mem_mod
from kokoro import portrait_controller
from kokoro import pool as pool_mod
from kokoro import impulse as impulse_mod
from kokoro import screen_interest
from kokoro import state_machine as sm
from kokoro import subtitle as subtitle_mod
from kokoro import stt as stt_mod
from kokoro import tts as tts_mod
from kokoro import user_commands
from kokoro import agent_loop
from kokoro import bilibili_live as bilibili_live_mod
from kokoro import edge_cache as edge_cache_mod
from kokoro import token_usage
from kokoro import tool_registry as tool_registry_mod


_PAREN_STRIP_RE = re.compile(r"\s*[\uff08(][^\uff09)]*[\uff09)]\s*")

def _strip_parens(text: str) -> str:
    return _PAREN_STRIP_RE.sub("", text).strip()


class _ParenFilter:
    """Stateful filter to remove parenthetical content during streaming."""

    def __init__(self):
        self._depth = 0

    def filter(self, text: str) -> str:
        result: list[str] = []
        for ch in text:
            if ch in "\uff08(":
                self._depth += 1
            elif ch in "\uff09)":
                if self._depth > 0:
                    self._depth -= 1
            elif self._depth == 0:
                result.append(ch)
        return "".join(result)


CONFIG = cfg.load()


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
    root = os.path.dirname(os.path.abspath(__file__))
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
    parser.add_argument("--no-tts", action="store_true", help="Disable speech output")
    parser.add_argument("--no-portrait", action="store_true", help="Disable portrait overlay")
    parser.add_argument("--no-impulse", action="store_true", help="Disable impulse planner")
    parser.add_argument("--no-screen-watch", action="store_true", help="Disable screen context watcher")
    parser.add_argument("--no-tools", action="store_true", help="Disable tool calling (use legacy regex commands)")
    parser.add_argument("--bilibili-room", type=int, default=None, help="Bilibili live room ID (overrides config)")
    return parser.parse_args()


def display_user(text: str) -> None:
    sys.stdout.write(f"\r\033[K[User] {text}\n")
    sys.stdout.flush()


def chat_stream(
    messages: list[dict],
    char_name: str,
    model: str,
    tts_engine: object | None,
    cancel_event: threading.Event | None = None,
    character_config: dict | None = None,
    agent_config: agent_loop.AgentConfig | None = None,
    tool_context: dict | None = None,
    usage_callback=None,
    subtitle_client=None,
) -> tuple[str, bool]:
    """Stream LLM response. Returns (reply_text, was_cancelled)."""
    print(f"\n{char_name}: ", end="", flush=True)
    char_cfg = character_config or {}
    _llm_api_base = char_cfg.get("llm_url") or None
    # Resolve API key by model: DeepSeek is handled in api_headers(),
    # CharGLM uses its own named config key, other local models need none.
    model_lower = model.lower()
    if model_lower.startswith("charglm"):
        _llm_api_key = cfg.charglm_api_key() or None
    else:
        _llm_api_key = None

    if agent_config is not None:
        # Tool-calling path: agent loop handles streaming + tools
        t0 = time.perf_counter()
        result = agent_loop.agent_chat(
            messages, model,
            agent_config=agent_config,
            cancel_event=cancel_event,
            tts_engine=tts_engine,
            character_config=character_config,
            api_base_url=_llm_api_base,
            api_key=_llm_api_key,
            usage_callback=usage_callback,
            **(tool_context or {}),
        )
        if not result.cancelled:
            print()
            print(f"  [latency] llm_done {time.perf_counter() - t0:.2f}s")
            if result.tool_calls_made > 0:
                print(f"  [tool] total tool calls: {result.tool_calls_made}")
        reply = _strip_parens(result.reply)
        if reply and tts_engine and not result.cancelled:
            tts_engine.end_sentence()
        return reply, result.cancelled

    # Legacy path: plain streaming
    reply = ""
    t0 = time.perf_counter()
    first_token_at = 0.0
    paren_filter = _ParenFilter()
    cancelled = False

    for content in llm_client.stream_chat(
        messages, model,
        cancel_event=cancel_event,
        api_base_url=_llm_api_base,
        api_key=_llm_api_key,
        usage_callback=usage_callback,
    ):
        content = paren_filter.filter(content)
        if not content:
            continue
        if not first_token_at:
            first_token_at = time.perf_counter()
            print(f"\n  [latency] llm_first_token {first_token_at - t0:.2f}s")
            print(f"{char_name}: ", end="", flush=True)
        print(content, end="", flush=True)
        reply += content
        if tts_engine:
            tts_engine.push(content)
        if subtitle_client:
            subtitle_client.push_text(content, mode="append")

    if cancel_event and cancel_event.is_set():
        cancelled = True
        print(f"\n  [interrupt] barge-in, cancelled after {time.perf_counter() - t0:.1f}s")
    else:
        print()
    reply = _strip_parens(reply)
    if reply and tts_engine and not cancelled:
        tts_engine.end_sentence()
    if not cancelled:
        print(f"  [latency] llm_done {time.perf_counter() - t0:.2f}s")
    return reply, cancelled


def create_tts_engine(enabled: bool, voice_id: str | None = None):
    if not enabled:
        return None
    try:
        tts_mod.warmup()
        engine = tts_mod.StreamingTTS(voice=voice_id)
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

    # ── state machine ──────────────────────────────────────────────────────
    machine = sm.SystemStateMachine()

    # ── observers: state change → side effects ─────────────────────────────
    def on_state_change(old: sm.SystemState, new: sm.SystemState, event: sm.SystemEvent) -> None:
        if new == sm.SystemState.ERROR:
            print(f"\n  [state] ERROR (from {old.value} via {event.value})")

    machine.subscribe(on_state_change)

    # ── memory backend ─────────────────────────────────────────────────────
    memory_backend = mem_mod.create_backend(CONFIG)
    try:
        session = chat_session.load_session(args.character, memory_backend)
    except KeyError:
        from kokoro import character
        print(f"[error] Character '{args.character}' not found")
        print(f"Available characters: {', '.join(character.load().keys())}")
        return

    # Conversation summary persistence
    summary_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    session.summary_file = os.path.join(summary_dir, f"summary_{args.character}.json")
    session.load_summary()

    model = args.model or session.character_config.get("llm_model") or cfg.llm_model()
    tts_engine = create_tts_engine(not args.no_tts, session.character_data.get("tts_voice_id"))

    # ── AEC (Acoustic Echo Cancellation) ────────────────────────────────────────
    _aec_processor = None
    if cfg.aec_enabled() and tts_engine is not None:
        try:
            from kokoro.aec import AECProcessor

            tts_sr = tts_mod.SAMPLE_RATE
            _aec_processor = AECProcessor(
                mic_sample_rate=stt_mod.SAMPLE_RATE,
                tts_sample_rate=tts_sr,
                ns_level=cfg.aec_ns_level(),
            )
            _aec_processor.set_delay(cfg.aec_delay_ms())
            tts_engine.on_audio_frame = _aec_processor.push_reference
            print(f"  [aec] enabled (playback_sr={tts_sr}, mic_sr={stt_mod.SAMPLE_RATE}, delay={cfg.aec_delay_ms()}ms)")
        except Exception as exc:
            print(f"  [aec] init failed: {exc}")
            _aec_processor = None
    portrait_client = None
    portrait_worker = None
    if not args.no_portrait:
        try:
            portrait_client, portrait_worker = portrait_controller.create_controller(args.character, model)
            machine.set_portrait_state(sm.PortraitState.SLIDESHOW)
        except Exception as exc:
            print(f"  [cli] Portrait overlay init failed: {exc}")

    # ── subtitle overlay (tied to portrait on/off) ────────────────────────────
    _subtitle_client: subtitle_mod.SubtitleOverlayClient | None = None
    _stt_subtitle_client: subtitle_mod.SubtitleOverlayClient | None = None
    if not args.no_portrait:
        _subtitle_host = str(cfg.get("subtitle", {}).get("subtitle_host", "127.0.0.1"))
        _subtitle_port = int(cfg.get("subtitle", {}).get("subtitle_port", 17353))
        _subtitle_client = subtitle_mod.SubtitleOverlayClient(host=_subtitle_host, port=_subtitle_port)
        _subtitle_client.start()

        # STT subtitle overlay (separate instance, dark blue)
        _stt_host = str(cfg.get("subtitle_stt", {}).get("subtitle_host", "127.0.0.1"))
        _stt_port = int(cfg.get("subtitle_stt", {}).get("subtitle_port", 17354))
        _stt_subtitle_client = subtitle_mod.SubtitleOverlayClient(host=_stt_host, port=_stt_port)
        _stt_subtitle_client.start(config_prefix="subtitle_stt")


    # ── tool calling ──────────────────────────────────────────────────────────
    _agent_config: agent_loop.AgentConfig | None = None
    _tool_enabled = cfg.tool_enabled() and not args.no_tools
    if _tool_enabled:
        _tool_list = cfg.tool_list()
        _tool_timeout = cfg.tool_timeout()
        _max_iter = cfg.tool_max_iterations()
        _registry = tool_registry_mod.create_registry(
            tool_list=_tool_list,
            tool_timeout=_tool_timeout,
        )
        _tool_schemas = _registry.enabled_schemas()
        if _tool_schemas:
            _agent_config = agent_loop.AgentConfig(
                tools=_tool_schemas,
                tool_registry=_registry,
                max_tool_iterations=_max_iter,
                tool_timeout=_tool_timeout,
                subtitle_client=_subtitle_client,
            )
            print(f"  [tool] Enabled: {', '.join(s['function']['name'] for s in _tool_schemas)}")
        else:
            _tool_enabled = False
            print("  [tool] No tools enabled (check config or model compatibility)")

    screen_cfg = CONFIG.get("screen_watch", {})
    if not isinstance(screen_cfg, dict):
        screen_cfg = {}
    screen_watch_enabled = bool(screen_cfg.get("enabled", False))
    if args.no_screen_watch:
        screen_watch_enabled = False
    screen_watch_interval = max(10.0, float(screen_cfg.get("watch_interval", 45.0)))
    screen_interest_threshold = max(0.0, float(screen_cfg.get("interest_threshold", 70.0)))
    screen_vision_timeout = max(5, int(screen_cfg.get("vision_timeout", 45)))
    edge_cache_config = edge_cache_mod.config_from_dict(CONFIG)
    memory_detector = memory_events.from_config(CONFIG, memory_backend, session.character_id)

    # ── shared cancel token for barge-in ──────────────────────────────────────
    _current_cancel: list[threading.Event | None] = [None]

    # ── Bilibili live manager (connection only, impulse drives replies) ────
    _bilibili_manager: bilibili_live_mod.BilibiliLiveManager | None = None
    _bilibili_room_id_raw = args.bilibili_room if args.bilibili_room is not None else cfg.bilibili_live_room_id()
    _bilibili_enabled = cfg.bilibili_live_enabled() and _bilibili_room_id_raw > 0
    _bilibili_live_mode = cfg.bilibili_live_live_mode()
    if _bilibili_enabled:
        _bilibili_manager = bilibili_live_mod.BilibiliLiveManager(
            room_id=_bilibili_room_id_raw,
            buffer_max_age=cfg.bilibili_live_buffer_max_age(),
        )
        _bilibili_manager.start()
    elif args.bilibili_room is not None and _bilibili_room_id_raw > 0:
        print(f"  [bilibili] Room {_bilibili_room_id_raw} set but bilibili_live.enabled = false in config")
        _bilibili_enabled = False

    # ── impulse planner ─────────────────────────────────────────────────────
    _stt_refine_mode = cfg.stt_refine_mode()
    _stt_refine_inline = _stt_refine_mode == "inline"
    _use_impulse = cfg.impulse_enabled() and not args.no_impulse
    if _use_impulse:
        machine.set_proactive_state(sm.ProactiveState.ACCRUING)
        _impulse = impulse_mod.ImpulsePlanner(
            config=CONFIG,
            session=session,
            model=model,
            tts_engine=tts_engine,
            portrait_worker=portrait_worker,
            machine=machine,
            agent_config=_agent_config,
            cancel_slot=_current_cancel,
            memory_backend=memory_backend,
            chat_stream_fn=chat_stream,
            stt_refine_inline=_stt_refine_inline,
            bilibili_manager=_bilibili_manager,
            live_mode=_bilibili_live_mode,
            subtitle_client=_subtitle_client,
        )
    else:
        _impulse = None
        machine.set_proactive_state(sm.ProactiveState.DISABLED)
    # ── conversation handler (called by pool when STT text is ready) ───────
    def on_refined(text: str) -> None:
        if _stt_subtitle_client:
            _stt_subtitle_client.clear()
        display_user(text)
        if _impulse is not None:
            _impulse.reset()

        # Emit state transition: → THINKING
        if not machine.emit(sm.SystemEvent.STT_REFINED):
            # 状态机拒绝 — 系统正忙(THINKING/SPEAKING)，barge-in 让路
            if machine.is_busy:
                cancel = _current_cancel[0]
                if cancel:
                    cancel.set()
                if tts_engine:
                    tts_engine.interrupt()
                if _aec_processor is not None:
                    _aec_processor.reset()
                machine.emit(sm.SystemEvent.USER_SPEECH_START)
                machine.set_stt_state(sm.STTState.LISTENING)
                # 重试：机器已进入 LISTENING，STT_REFINED 应该可以通过了
                if not machine.emit(sm.SystemEvent.STT_REFINED):
                    return
            else:
                return

        # 恢复 TTS 连接（中断后 WS 已死，prepare 内会自动重建）
        if tts_engine:
            try:
                tts_engine.prepare()
            except Exception as exc:
                print(f"\n  [tts] prepare failed: {exc}")

        cancel_event = threading.Event()
        _current_cancel[0] = cancel_event

        try:
            command_context = ""
            if not _tool_enabled:
                command = user_commands.detect(text)
                if command:
                    print(f"\n  [command] {command.type} confidence={command.confidence:.2f}")

                    # Start vision immediately, parallel with waiting-reply LLM + TTS
                    vision_result: list[user_commands.CommandResult | Exception | None] = [None]
                    vision_ready = threading.Event()

                    def _run_vision() -> None:
                        try:
                            vision_result[0] = user_commands.execute(command, timeout=screen_vision_timeout)
                        except Exception as exc:
                            vision_result[0] = exc
                        finally:
                            vision_ready.set()

                    threading.Thread(target=_run_vision, daemon=True).start()

                    try:
                        waiting_reply = user_commands.build_waiting_reply(
                            text,
                            session.history,
                            llm_url=refine_url,
                            llm_model=refine_model,
                            character_name=session.character_name,
                            character_prompt=session.system_prompt,
                            api_key=refine_key,
                        )
                    except Exception:
                        waiting_reply = prompts.get("cli.waiting_reply_fallback", "好，我看一下。")

                    print(f"\n{session.character_name}: {waiting_reply}")
                    if tts_engine:
                        tts_engine.push(waiting_reply)
                        while tts_engine.is_playing and not cancel_event.is_set():
                            time.sleep(0.1)

                    if cancel_event.is_set():
                        _current_cancel[0] = None
                        return

                    vision_ready.wait()
                    result = vision_result[0]
                    if isinstance(result, Exception):
                        command_context = ""
                        print(f"\n  [screen] command error: {result}")
                    else:
                        command_context = result.context
                        if result.ok and result.screen_context:
                            session.add_screen_context(result.screen_context)
                            print(f"\n  [screen] command interest={result.score:.1f} {result.screen_context.split(chr(10))[0]}")
                        elif result.user_visible_note:
                            label = "private" if result.private else "error"
                            print(f"\n  [screen] command {label}: {result.user_visible_note}")
                            command_context = result.context or result.user_visible_note

            # Live mode: inject co-streaming context so the AI knows user speech
            # might be directed at viewers, not necessarily at the AI.
            bilibili_ctx = prompts.get("cli.bilibili_live_context", "")
            if _bilibili_enabled and _bilibili_live_mode and command_context:
                command_context = f"{bilibili_ctx}\n\n{command_context}"
            elif _bilibili_enabled and _bilibili_live_mode:
                command_context = bilibili_ctx

            messages = session.build_messages(text, extra_context=command_context, stt_refine_inline=_stt_refine_inline, inject_memory=not _tool_enabled)

            try:
                reply, cancelled = chat_stream(messages, session.character_name, model, tts_engine, cancel_event=cancel_event, character_config=session.character_config, agent_config=_agent_config, usage_callback=token_usage.make_callback(model, "chat"), tool_context=dict(session=session, memory_backend=memory_backend, character_id=session.character_id), subtitle_client=_subtitle_client)
            except requests.exceptions.ConnectionError:
                print(f"\n[connection failed] Cannot connect to {llm_client.api_base_for(model)}")
                machine.emit_error("llm_connection")
                _current_cancel[0] = None
                return
            except Exception as exc:
                print(f"\n[error] {type(exc).__name__}: {exc}")
                machine.emit_error("llm_stream")
                _current_cancel[0] = None
                return

            if cancelled:
                # User interrupted — STT worker already transitioned to LISTENING
                _current_cancel[0] = None
                return

            # LLM done → transition to SPEAKING
            machine.emit(sm.SystemEvent.LLM_DONE)

            # Trigger impulse planning immediately after LLM output (don't wait for TTS)
            if _impulse is not None:
                _impulse.on_conversation_end()

            if tts_engine:
                machine.set_tts_state(sm.TTSState.STREAMING)

            session.remember(text, reply, async_store=True)
            if portrait_worker:
                portrait_worker.submit(text, reply)

            if tts_engine:
                while tts_engine.is_playing and not cancel_event.is_set():
                    time.sleep(0.1)
                if cancel_event.is_set():
                    _current_cancel[0] = None
                    return
                tts_engine.prepare()

            # TTS done → back to IDLE
            machine.set_tts_state(sm.TTSState.IDLE)
            machine.emit(sm.SystemEvent.TTS_DONE)
            machine.reset_error_count()

            # Clear subtitle
            if _subtitle_client:
                _subtitle_client.clear()

        except Exception as exc:
            print(f"\n[error] on_refined: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            machine.emit_error("on_refined")
        finally:
            _current_cancel[0] = None

    # ── STT pool ───────────────────────────────────────────────────────────
    refine_url, refine_model, refine_key = refine_endpoint()
    pool = pool_mod.ConversationPool(
        llm_url=refine_url,
        llm_model=refine_model,
        on_refined=on_refined,
        api_key=refine_key,
        mode=_stt_refine_mode,
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
    print(f"  Impulse: {_use_impulse}")
    print(f"  Tool calling: {_tool_enabled}")
    print(f"  Screen watch: {screen_watch_enabled}")
    print(f"  Edge page cache: {edge_cache_config.enabled}")
    print(f"  Memory events: {memory_detector.config.enabled}")
    print(f"  Bilibili live: {_bilibili_enabled and _bilibili_manager is not None} (live_mode={_bilibili_live_mode})")
    print(f"  Subtitle: {_subtitle_client is not None} | STT subtitle: {_stt_subtitle_client is not None}")
    aec_enabled_str = "enabled" if _aec_processor is not None else "disabled"
    print(f"  AEC: {aec_enabled_str}")
    print("  Ctrl+C to stop")
    print("=" * 50)

    greeting = session.character_data.get("greeting")
    if greeting:
        print(f"\n{session.character_name}: {greeting}")

    # ── system ready ───────────────────────────────────────────────────────
    machine.emit(sm.SystemEvent.INIT_DONE)

    # ── STT worker ─────────────────────────────────────────────────────────
    last_partial = ""
    pause_during_tts = cfg.stt_pause_during_tts()
    if _aec_processor is not None:
        pause_during_tts = False  # AEC 处理回声，不需要暂停 STT

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

            while not machine.is_shutting_down:
                chunk, _ = audio_stream.read(1600)

                if pause_during_tts and tts_engine and tts_engine.is_playing:
                    tts_was_playing = True
                    continue

                if tts_was_playing:
                    tts_was_playing = False
                    stt_stream = recognizer.create_stream()
                    last_partial = ""
                    if _stt_subtitle_client:
                        _stt_subtitle_client.clear()
                    continue

                if _aec_processor is not None:
                    mono = _aec_processor.process(chunk[:, 0])
                else:
                    mono = stt_mod.denoise(chunk[:, 0])
                stt_stream.accept_waveform(stt_mod.SAMPLE_RATE, mono)

                if recognizer.is_ready(stt_stream):
                    recognizer.decode_stream(stt_stream)
                    text = recognizer.get_result(stt_stream)
                    if text:
                        pool.add_chunk(text)
                        if text != last_partial:
                            if _stt_subtitle_client:
                                _stt_subtitle_client.push_text(text, mode="set")
                            sys.stdout.write(f"\r\033[K  [STT] {text}")
                            sys.stdout.flush()
                            last_partial = text
                            # Signal that user is speaking
                            if machine.is_idle or machine.state == sm.SystemState.SCREEN_WATCHING:
                                machine.emit(sm.SystemEvent.USER_SPEECH_START)
                                machine.set_stt_state(sm.STTState.LISTENING)
                            elif machine.is_thinking or machine.is_speaking:
                                # 不立即 barge-in — 等 endpoint 拿到完整文本后再处理
                                # 避免"爱"这种不完整片段提前取消 LLM
                                pass
                    if recognizer.is_endpoint(stt_stream):
                        if machine.is_busy:
                            # 系统忙时不 reset 识别器 — 用户可能继续说
                            # 不清空流，让新文本"爱丽丝"自然延续"晚上好"成为"晚上好爱丽丝"
                            pass
                        else:
                            if _stt_subtitle_client:
                                _stt_subtitle_client.clear()
                            recognizer.reset(stt_stream)
                            last_partial = ""
                            machine.emit(sm.SystemEvent.USER_SPEECH_END)
        except Exception as exc:
            print(f"\n[STT error] {exc}")
            traceback.print_exc()
            machine.emit_error("stt")
        finally:
            if audio_stream is not None:
                try:
                    audio_stream.stop()
                    audio_stream.close()
                except Exception:
                    pass

    stt_thread = threading.Thread(target=stt_worker, daemon=True)
    stt_thread.start()
    def screen_cache_worker() -> None:
        """Continuous screen capture -> analyze -> cache loop.

        Runs back-to-back.  watch_interval is the *minimum* delay between
        captures; if analysis takes longer no extra wait is added.
        """
        from kokoro.screen_interest import get_cache
        sc = get_cache()
        while not machine.is_shutting_down:
            if not screen_watch_enabled:
                time.sleep(1.0)
                continue

            t0 = time.perf_counter()
            try:
                result = screen_interest.analyze(timeout=screen_vision_timeout)
            except Exception as exc:
                print(f"\n[screen watch error] {type(exc).__name__}: {exc}")
                time.sleep(5.0)
                continue

            # Always update cache (impulse reads from here)
            sc.put(result)

            # Add high-interest results to session context
            if not machine.is_busy and result.score >= screen_interest_threshold:
                context = result.content or result.reason
                session.add_screen_context(context)
                print(f"\n  [screen] interest={result.score:.1f} {context.split(chr(10))[0]}")

            # Head-to-tail: minimum interval between captures
            elapsed = time.perf_counter() - t0
            if elapsed < screen_watch_interval:
                time.sleep(screen_watch_interval - elapsed)

    screen_thread = threading.Thread(target=screen_cache_worker, daemon=True)
    screen_thread.start()

    def edge_page_cache_worker() -> None:
        last_cache_signature = ""
        last_error_message = ""
        while not machine.is_shutting_down:
            if not edge_cache_config.enabled:
                time.sleep(1.0)
                continue

            t0 = time.perf_counter()
            try:
                payload = edge_cache_mod.capture_and_save(edge_cache_config)
                last_error_message = ""
                title = payload.get("tab", {}).get("title") or "(untitled)"
                tab = payload.get("tab", {})
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
            if elapsed < edge_cache_config.interval_seconds:
                time.sleep(edge_cache_config.interval_seconds - elapsed)

    edge_cache_thread = threading.Thread(target=edge_page_cache_worker, daemon=True)
    edge_cache_thread.start()

    # ── memory event worker ────────────────────────────────────────────────
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

    memory_thread = threading.Thread(target=memory_event_worker, daemon=True)
    memory_thread.start()

    # ── error recovery watcher ─────────────────────────────────────────────
    def error_recovery_worker() -> None:
        while not machine.is_shutting_down:
            time.sleep(1.0)
            if machine.state == sm.SystemState.ERROR:
                # Perform cleanup
                if tts_engine:
                    try:
                        tts_engine.prepare()
                    except Exception:
                        pass
                machine.recover_from_error()
                machine.reset_error_count()

    error_thread = threading.Thread(target=error_recovery_worker, daemon=True)
    error_thread.start()

    # ── first impulse trigger (system is idle, start planning) ────────────
    if _use_impulse:
        _impulse.on_conversation_end()

    # ── main loop ──────────────────────────────────────────────────────────
    try:
        while not machine.is_shutting_down:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n\n[cli] Stopping...")
    finally:
        machine.emit(sm.SystemEvent.SHUTDOWN)
        pool.stop()
        if _bilibili_manager is not None:
            _bilibili_manager.stop()
        print()
        print(token_usage.summary())
        if portrait_worker:
            portrait_worker.stop()
        if portrait_client:
            portrait_client.shutdown()
        if _subtitle_client:
            _subtitle_client.shutdown()
        if _stt_subtitle_client:
            _stt_subtitle_client.shutdown()
        if tts_engine:
            tts_engine.close()


if __name__ == "__main__":
    _cli_log, _stdout, _stderr = _install_cli_log()
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nBye.")
    except Exception as exc:
        print(f"\n[error] {type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        time.sleep(0.3)
        sys.stdout = _stdout
        sys.stderr = _stderr
        _cli_log.close()
