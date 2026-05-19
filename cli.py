#!/usr/bin/env python3
"""Voice-first CLI entrypoint.

CLI owns microphone/STT orchestration. Chat, memory, model routing, and TTS
helpers live in kokoro package modules so they can be shared.  State machine
(kokoro/state_machine.py) is the single source of truth for what the system is
doing. All workers consult it instead of ad-hoc flags."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
import traceback
import threading
from collections import deque
from datetime import datetime

import requests

from kokoro import chat_session
from kokoro import console as console_mod
from kokoro import config as cfg
from kokoro import conversation as conversation_mod
from kokoro import dialogue_orchestrator as dialogue_mod
from kokoro import llm_client
from kokoro import prompts
from kokoro import memory_events
from kokoro import memory as mem_mod
from kokoro import multi_chat as multi_chat_mod
from kokoro import portrait_controller
from kokoro import screen_interest
from kokoro import scene as scene_mod
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
from kokoro import task_manager as task_manager_mod
from kokoro import vts_controller as vts_mod


_PAREN_STRIP_RE = re.compile(r"\s*[\uff08(][^\uff09)]*[\uff09)]\s*")

def _strip_parens(text: str) -> str:
    return _PAREN_STRIP_RE.sub("", text).strip()


_ECHO_TEXT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def _normalize_echo_text(text: str) -> str:
    return _ECHO_TEXT_RE.sub("", (text or "").lower())


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
    parser.add_argument("--no-proactive", action="store_true", help="Disable proactive dialogue")
    parser.add_argument("--no-screen-watch", action="store_true", help="Disable screen context watcher")
    parser.add_argument("--no-tools", action="store_true", help="Disable tool calling (use legacy regex commands)")
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


def merge_stt_text(prev: str, new: str) -> str:
    prev = (prev or "").strip()
    new = (new or "").strip()
    if not prev:
        return new
    if not new:
        return prev
    if new in prev:
        return prev
    if prev in new:
        return new
    max_overlap = min(len(prev), len(new))
    for n in range(max_overlap, 0, -1):
        if prev[-n:] == new[:n]:
            return prev + new[n:]
    return prev + new


def stt_turn_deadline_delay(text: str) -> float:
    delay = cfg.stt_turn_merge_seconds()
    if len((text or "").strip()) <= cfg.stt_short_utterance_max_chars():
        delay += cfg.stt_short_utterance_extra_seconds()
    return delay


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

    if args.multi:
        _run_multi_cli(args)
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

    dialogue_model = args.model or session.character_config.get("llm_model") or cfg.dialogue_model()
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
            portrait_client, portrait_worker = portrait_controller.create_controller(args.character, dialogue_model)
            machine.set_portrait_state(sm.PortraitState.SLIDESHOW)
        except Exception as exc:
            print(f"  [cli] Portrait overlay init failed: {exc}")

    # ── VTube Studio ─────────────────────────────────────────────────────────
    _vts_controller: vts_mod.VTSController | None = None
    _vts_arbiter: vts_mod.VTSExpressionArbiter | None = None
    _vts_idle_loop: vts_mod.VTSIdleLoop | None = None
    _vts_lipsync: vts_mod.VTSLipSync | None = None
    _vts_loop: asyncio.AbstractEventLoop | None = None
    _vts_loop_thread: threading.Thread | None = None

    vts_cfg = CONFIG.get("vts", {})
    if vts_cfg.get("enabled", True):
        try:
            _vts_controller = vts_mod.VTSController(
                host=str(vts_cfg.get("host", "localhost")),
                port=int(vts_cfg.get("port", 8001)),
                character_id=args.character,
            )
            _vts_loop = asyncio.new_event_loop()
            _vts_loop_thread = threading.Thread(target=_vts_loop.run_forever, daemon=True)
            _vts_loop_thread.start()

            auth_future = asyncio.run_coroutine_threadsafe(
                _vts_controller.authenticate(), _vts_loop,
            )
            auth_future.result(timeout=10)

            _vts_arbiter = vts_mod.VTSExpressionArbiter(_vts_controller)
            asyncio.run_coroutine_threadsafe(_vts_arbiter.start(), _vts_loop)

            _vts_idle_loop = vts_mod.VTSIdleLoop(_vts_arbiter)
            asyncio.run_coroutine_threadsafe(_vts_idle_loop.start(), _vts_loop)

            _vts_lipsync = vts_mod.VTSLipSync(_vts_controller, _vts_arbiter, loop=_vts_loop)

            if tts_engine is not None:
                _original_audio_frame = tts_engine.on_audio_frame
                def _vts_audio_wrapper(chunk):
                    _vts_lipsync.on_audio_frame(chunk)
                    if _original_audio_frame:
                        _original_audio_frame(chunk)
                tts_engine.on_audio_frame = _vts_audio_wrapper

            def _on_vts_emotion(tone: str, motivation: str) -> None:
                if _vts_arbiter is None:
                    return
                expr_id = _vts_controller.resolve_emotion_tone(tone) if _vts_controller else None
                if expr_id:
                    params = _vts_controller.get_expression_params(expr_id, 1.0)
                    _vts_arbiter.set_layer("emotion", params)
                else:
                    _vts_arbiter.clear_layer("emotion")

            if session is not None and hasattr(session, "emotion"):
                session.emotion._on_update = _on_vts_emotion

            def _tts_state_monitor():
                was_active = False
                while not machine.is_shutting_down and _vts_idle_loop:
                    is_active = bool(tts_engine and tts_engine.is_playing)
                    _vts_idle_loop.set_tts_active(is_active)
                    if is_active and not was_active:
                        if _vts_lipsync:
                            _vts_lipsync.start()
                    elif not is_active and was_active:
                        if _vts_lipsync:
                            _vts_lipsync.stop()
                    was_active = is_active
                    time.sleep(0.5)
            if _vts_idle_loop:
                threading.Thread(target=_tts_state_monitor, daemon=True).start()

            print("  [vts] VTube Studio connected")
        except Exception as exc:
            print(f"  [vts] Init failed: {exc}")
            _vts_controller = None
            _vts_arbiter = None
            _vts_idle_loop = None
            _vts_lipsync = None

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
    _task_manager = task_manager_mod.TaskManager()
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
    recent_tts_texts: deque[tuple[float, str]] = deque(maxlen=12)
    recent_tts_lock = threading.Lock()

    def remember_tts_text(text: str) -> None:
        norm = _normalize_echo_text(text)
        if not norm:
            return
        now = time.monotonic()
        with recent_tts_lock:
            recent_tts_texts.append((now, norm))
            while recent_tts_texts and now - recent_tts_texts[0][0] > 8.0:
                recent_tts_texts.popleft()

    def is_probable_tts_echo(text: str) -> bool:
        norm = _normalize_echo_text(text)
        if len(norm) < 2:
            return False
        now = time.monotonic()
        with recent_tts_lock:
            while recent_tts_texts and now - recent_tts_texts[0][0] > 8.0:
                recent_tts_texts.popleft()
            for _, spoken in recent_tts_texts:
                if len(norm) < 8:
                    if norm == spoken or (
                        len(norm) >= 2 and (spoken.startswith(norm) or spoken.endswith(norm))
                    ):
                        return True
                    continue
                if norm in spoken or spoken in norm:
                    return True
        return False

    # ── Bilibili live manager ──────────────────────────────────────────────
    _bilibili_manager: bilibili_live_mod.BilibiliLiveManager | None = None
    _bilibili_room_id_raw = args.bilibili_room if args.bilibili_room is not None else cfg.bilibili_live_room_id()
    _bilibili_enabled = cfg.bilibili_live_enabled() and _bilibili_room_id_raw > 0
    _bilibili_live_mode = scene_mod.live_enabled(CONFIG)
    if _bilibili_enabled:
        _bilibili_manager = bilibili_live_mod.BilibiliLiveManager(
            room_id=_bilibili_room_id_raw,
            buffer_max_age=cfg.bilibili_live_buffer_max_age(),
        )
        _bilibili_manager.start()
    elif args.bilibili_room is not None and _bilibili_room_id_raw > 0:
        print(f"  [bilibili] Room {_bilibili_room_id_raw} set but bilibili_live.enabled = false in config")
        _bilibili_enabled = False

    # ── dialogue orchestrator + proactive speech ────────────────────────────
    _stt_refine_mode = cfg.stt_refine_mode()
    _stt_refine_inline = _stt_refine_mode == "inline"
    _dialogue = dialogue_mod.DialogueOrchestrator(
        config=CONFIG,
        session=session,
        model=dialogue_model,
        memory_backend=memory_backend,
    )
    _use_proactive = cfg.proactive_enabled() and not args.no_proactive
    machine.set_proactive_state(sm.ProactiveState.ACCRUING if _use_proactive else sm.ProactiveState.DISABLED)
    _pending_user_turn = {"text": "", "deadline": 0.0, "reason": "endpoint"}
    _pending_user_lock = threading.Lock()

    def _maybe_flush_user_turn(force: bool = False) -> None:
        with _pending_user_lock:
            deadline = float(_pending_user_turn.get("deadline", 0.0) or 0.0)
            if not force and (not deadline or time.monotonic() < deadline):
                return
            text = _pending_user_turn["text"].strip()
            _pending_user_turn["text"] = ""
            _pending_user_turn["deadline"] = 0.0
            _pending_user_turn["reason"] = "endpoint"
        if not text:
            return
        if not cfg.stt_dialogue_pool_enabled():
            if _stt_subtitle_client:
                _stt_subtitle_client.clear()
            display_user(text)
            _dialogue.cancel_plans()
            if not machine.emit(sm.SystemEvent.STT_REFINED):
                return
            conversation.reset_stream()
            threading.Thread(
                target=_handle_conversation,
                args=(text,),
                daemon=True,
            ).start()
            return
        threading.Thread(
            target=_handle_stt_pool_turn,
            args=(text,),
            daemon=True,
        ).start()
    # ── conversation handler (fast path: in STT thread, must not block) ─────
    def on_user_utterance(text: str) -> None:
        if is_probable_tts_echo(text):
            if _stt_subtitle_client:
                _stt_subtitle_client.clear()
            print("\n  [stt] dropped probable tts echo")
            conversation.reset_stream()
            machine.set_stt_state(sm.STTState.LISTENING)
            return
        reason = getattr(conversation, 'last_reason', 'endpoint')
        is_overlap = machine.tts_state in (sm.TTSState.STREAMING, sm.TTSState.DRAINING)

        # Handle barge-in quickly — just signal, don't block
        if machine.is_busy:
            cancel = _current_cancel[0]
            if cancel:
                cancel.set()
            if tts_engine:
                if is_overlap and "hard_break" in reason:
                    tts_engine.interrupt()
                elif is_overlap:
                    tts_engine.soft_interrupt()
                else:
                    tts_engine.interrupt()
            if _aec_processor is not None:
                _aec_processor.reset()
            machine.emit(sm.SystemEvent.USER_SPEECH_START)
            machine.set_stt_state(sm.STTState.LISTENING)

        with _pending_user_lock:
            _pending_user_turn["text"] = merge_stt_text(_pending_user_turn["text"], text)
            _pending_user_turn["reason"] = reason
            _pending_user_turn["deadline"] = time.monotonic() + stt_turn_deadline_delay(_pending_user_turn["text"])

    # ── conversation worker (runs in its own thread, may block) ────────────
    def _handle_stt_pool_turn(pool_text: str) -> None:
        cancel_event = threading.Event()
        _current_cancel[0] = cancel_event
        try:
            if not machine.emit(sm.SystemEvent.STT_REFINED):
                with _pending_user_lock:
                    _pending_user_turn["text"] = merge_stt_text(pool_text, _pending_user_turn["text"])
                    _pending_user_turn["deadline"] = time.monotonic() + stt_turn_deadline_delay(_pending_user_turn["text"])
                return

            decision = _dialogue.decide_stt_pool_turn(pool_text=pool_text)
            if cancel_event.is_set():
                return

            if decision.action == "wait":
                with _pending_user_lock:
                    _pending_user_turn["text"] = merge_stt_text(decision.remaining_text or pool_text, _pending_user_turn["text"])
                    _pending_user_turn["deadline"] = time.monotonic() + max(2.5, stt_turn_deadline_delay(_pending_user_turn["text"]))
                machine.emit(sm.SystemEvent.LLM_DONE)
                machine.set_tts_state(sm.TTSState.IDLE)
                machine.emit(sm.SystemEvent.TTS_DONE)
                machine.reset_error_count()
                machine.set_stt_state(sm.STTState.LISTENING)
                return

            consumed = decision.consumed_text.strip() or pool_text.strip()
            remaining = decision.remaining_text.strip()
            if remaining:
                with _pending_user_lock:
                    _pending_user_turn["text"] = merge_stt_text(remaining, _pending_user_turn["text"])
                    _pending_user_turn["deadline"] = time.monotonic() + stt_turn_deadline_delay(_pending_user_turn["text"])

            if _stt_subtitle_client:
                _stt_subtitle_client.clear()
            display_user(consumed)
            _dialogue.cancel_plans()
            machine.emit(sm.SystemEvent.LLM_DONE)

            # ── tool-calling path: route through agent loop ────────────────
            if _agent_config is not None and consumed:
                tool_names = [s.get("function", {}).get("name", "?") for s in (_agent_config.tools or [])]
                print(f"\n  [stt_pool] tool path: {len(tool_names)} tools registered in agent_config")
                print(f"  [stt_pool] tools: {', '.join(tool_names)}")
                msg_decision = dialogue_mod.DialogueDecision(
                    action="speak",
                    intent=str(decision.notes or "")[:80],
                )
                messages = _dialogue.build_reply_messages(
                    user_text=consumed,
                    decision=msg_decision,
                )
                try:
                    result, cancelled = chat_stream(
                        messages, session.character_name, dialogue_model, tts_engine,
                        cancel_event=cancel_event,
                        character_config=session.character_config,
                        agent_config=_agent_config,
                        usage_callback=token_usage.make_callback(dialogue_model, "stt_pool"),
                        tool_context=dict(
                            session=session,
                            memory_backend=memory_backend,
                            character_id=session.character_id,
                            vts_controller=_vts_controller,
                            vts_arbiter=_vts_arbiter,
                            event_loop=_vts_loop,
                            task_manager=_task_manager,
                        ),
                        subtitle_client=_subtitle_client,
                    )
                except requests.exceptions.ConnectionError:
                    print(f"\n[connection failed] Cannot connect to {llm_client.api_base_for(dialogue_model)}")
                    machine.emit_error("stt_pool_connection")
                    return
                except Exception as exc:
                    print(f"\n[error] stt_pool agent: {type(exc).__name__}: {exc}")
                    machine.emit_error("stt_pool_agent")
                    return

                if cancelled:
                    return
                reply = dialogue_mod.clean_generated_reply(result, session.character_name)
                if reply:
                    remember_tts_text(reply)
                if tts_engine and reply:
                    machine.set_tts_state(sm.TTSState.STREAMING)
                    _tts_say(tts_engine, reply, wait=False)
                    while tts_engine.is_playing and not cancel_event.is_set():
                        time.sleep(0.1)
                    if cancel_event.is_set():
                        return
                    if _aec_processor is not None and cfg.aec_auto_reset_on_tts_done():
                        _aec_processor.reset()
                    tts_engine.prepare()
                session.remember(consumed, reply, async_store=True)
                if portrait_worker:
                    portrait_worker.submit(consumed, reply)
                machine.set_tts_state(sm.TTSState.IDLE)
                machine.emit(sm.SystemEvent.TTS_DONE)
                machine.reset_error_count()
                machine.set_stt_state(sm.STTState.LISTENING)
                if _subtitle_client:
                    _subtitle_client.clear()
                return

            # ── legacy path: direct reply from planner ─────────────────────
            reply = dialogue_mod.clean_generated_reply(decision.reply, session.character_name)
            if reply:
                remember_tts_text(reply)
                print(f"\n{session.character_name}: {reply}")
            if tts_engine:
                machine.set_tts_state(sm.TTSState.STREAMING)
            session.remember(consumed, reply, async_store=True)
            if portrait_worker:
                portrait_worker.submit(consumed, reply)

            if tts_engine and reply:
                _tts_say(tts_engine, reply, wait=False)
                while tts_engine.is_playing and not cancel_event.is_set():
                    time.sleep(0.1)
                if cancel_event.is_set():
                    return
                if _aec_processor is not None and cfg.aec_auto_reset_on_tts_done():
                    _aec_processor.reset()
                tts_engine.prepare()

            machine.set_tts_state(sm.TTSState.IDLE)
            machine.emit(sm.SystemEvent.TTS_DONE)
            machine.reset_error_count()
            machine.set_stt_state(sm.STTState.LISTENING)
            if _subtitle_client:
                _subtitle_client.clear()
        except Exception as exc:
            print(f"\n[error] _handle_stt_pool_turn: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            machine.emit_error("_handle_stt_pool_turn")
        finally:
            _current_cancel[0] = None

    def _handle_conversation(text: str) -> None:
        cancel_event = threading.Event()
        _current_cancel[0] = cancel_event

        try:
            command_context = ""
            if not _tool_enabled:
                command = user_commands.detect(text)
                if command:
                    print(f"\n  [command] {command.type} confidence={command.confidence:.2f}")
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

            bilibili_ctx = prompts.get("cli.bilibili_live_context", "")
            if _bilibili_enabled and _bilibili_live_mode and command_context:
                command_context = f"{bilibili_ctx}\n\n{command_context}"
            elif _bilibili_enabled and _bilibili_live_mode:
                command_context = bilibili_ctx

            event = dialogue_mod.DialogueEvent(
                type="user_utterance",
                text=text,
                source="user",
                extra_context=command_context or "",
            )
            decision = _dialogue.decide(event)

            if decision.action in ("silence", "observe", "cancel_plan"):
                if decision.action == "cancel_plan":
                    _dialogue.cancel_plans()
                _dialogue.record_user_observation(text, decision)
                machine.emit(sm.SystemEvent.LLM_DONE)
                machine.set_tts_state(sm.TTSState.IDLE)
                machine.emit(sm.SystemEvent.TTS_DONE)
                machine.reset_error_count()
                return

            if decision.action == "schedule":
                _dialogue.record_user_observation(text, decision)
                _dialogue.add_plan(decision, created_from=text)
                machine.emit(sm.SystemEvent.LLM_DONE)
                machine.set_tts_state(sm.TTSState.IDLE)
                machine.emit(sm.SystemEvent.TTS_DONE)
                machine.reset_error_count()
                return

            messages = _dialogue.build_reply_messages(
                user_text=text,
                decision=decision,
                extra_context=command_context or None,
                max_history_messages=30 if ("总结" in text or "summary" in text.lower()) else None,
            )
            if _stt_refine_inline:
                inline_prompt = prompts.get("stt_refine_inline.system", "")
                if inline_prompt:
                    messages.insert(-1, {"role": "system", "content": inline_prompt})

            try:
                reply, cancelled = chat_stream(messages, session.character_name, dialogue_model, tts_engine, cancel_event=cancel_event, character_config=session.character_config, agent_config=_agent_config, usage_callback=token_usage.make_callback(dialogue_model, "chat"), tool_context=dict(
                    session=session,
                    memory_backend=memory_backend,
                    character_id=session.character_id,
                    vts_controller=_vts_controller,
                    vts_arbiter=_vts_arbiter,
                    event_loop=_vts_loop,
                    task_manager=_task_manager,
                ), subtitle_client=_subtitle_client)
            except requests.exceptions.ConnectionError:
                print(f"\n[connection failed] Cannot connect to {llm_client.api_base_for(dialogue_model)}")
                machine.emit_error("llm_connection")
                _current_cancel[0] = None
                return
            except Exception as exc:
                print(f"\n[error] {type(exc).__name__}: {exc}")
                machine.emit_error("llm_stream")
                _current_cancel[0] = None
                return

            if cancelled:
                _current_cancel[0] = None
                return

            reply = dialogue_mod.clean_generated_reply(reply, session.character_name)
            machine.emit(sm.SystemEvent.LLM_DONE)
            if reply:
                remember_tts_text(reply)

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
                if _aec_processor is not None and cfg.aec_auto_reset_on_tts_done():
                    _aec_processor.reset()
                tts_engine.prepare()

            machine.set_tts_state(sm.TTSState.IDLE)
            machine.emit(sm.SystemEvent.TTS_DONE)
            machine.reset_error_count()

            if _subtitle_client:
                _subtitle_client.clear()

        except Exception as exc:
            print(f"\n[error] _handle_conversation: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            machine.emit_error("_handle_conversation")
        finally:
            _current_cancel[0] = None

    # ── STT + ConversationManager ──────────────────────────────────────────
    def _execute_dialogue_plan(decision: dialogue_mod.DialogueDecision) -> None:
        if not machine.can_start_conversation:
            _dialogue.add_plan(decision, created_from="deferred_busy")
            return
        if not machine.emit(sm.SystemEvent.PROACTIVE_TRIGGERED):
            _dialogue.add_plan(decision, created_from="deferred_rejected")
            return

        machine.set_proactive_state(sm.ProactiveState.EXECUTING)
        cancel_event = threading.Event()
        _current_cancel[0] = cancel_event

        try:
            context = ""
            memory_query = " ".join(part for part in (decision.topic, decision.intent) if part)
            if memory_query:
                try:
                    memory_ctx = memory_backend.get_context(memory_query, user_id=session.character_id)
                except Exception:
                    memory_ctx = ""
                if memory_ctx:
                    context = memory_ctx
            continuation_guard = (
                "【续接约束】这是同一场景里稍后补充的一句新话。"
                "不要重复你上一句已经说过的内容，不要重说同一段开场，"
                "直接补充新的后半句、新的信息或新的角度；如果没有新的补充点，就宁可更短。"
            )
            context = f"{continuation_guard}\n\n{context}" if context else continuation_guard
            messages = _dialogue.build_reply_messages(
                user_text=prompts.get("dialogue_orchestrator.scheduled_user_prompt", "请直接说出现在要说的话。"),
                decision=decision,
                extra_context=context or None,
            )

            try:
                reply, cancelled = chat_stream(
                    messages,
                    session.character_name,
                    dialogue_model,
                    tts_engine,
                    cancel_event=cancel_event,
                    character_config=session.character_config,
                    agent_config=_agent_config,
                    usage_callback=token_usage.make_callback(dialogue_model, "dialogue_scheduled"),
                    tool_context=dict(
                        session=session,
                        memory_backend=memory_backend,
                        character_id=session.character_id,
                        vts_controller=_vts_controller,
                        vts_arbiter=_vts_arbiter,
                        event_loop=_vts_loop,
                        task_manager=_task_manager,
                    ),
                    subtitle_client=_subtitle_client,
                )
            except requests.exceptions.ConnectionError:
                print(f"\n[connection failed] Cannot connect to {llm_client.api_base_for(dialogue_model)}")
                machine.emit_error("dialogue_scheduled_connection")
                return
            except Exception as exc:
                print(f"\n[error] scheduled dialogue: {type(exc).__name__}: {exc}")
                machine.emit_error("dialogue_scheduled")
                return

            if cancelled:
                return

            reply = dialogue_mod.clean_generated_reply(reply, session.character_name)
            if reply:
                memory_trigger = "【主动对话】" + (decision.topic or decision.intent or "空闲时自然开口")
                session.remember(memory_trigger, reply, async_store=True)
                if portrait_worker:
                    portrait_worker.submit("", reply)

            machine.emit(sm.SystemEvent.LLM_DONE)

            if tts_engine:
                machine.set_tts_state(sm.TTSState.STREAMING)
                while tts_engine.is_playing and not cancel_event.is_set():
                    time.sleep(0.1)
                if cancel_event.is_set():
                    return
                tts_engine.prepare()

            machine.set_tts_state(sm.TTSState.IDLE)
            machine.emit(sm.SystemEvent.TTS_DONE)
            machine.reset_error_count()
            if _subtitle_client:
                _subtitle_client.clear()
        finally:
            _current_cancel[0] = None
            machine.set_proactive_state(sm.ProactiveState.ACCRUING if _use_proactive else sm.ProactiveState.DISABLED)

    _dialogue_executor_stop = threading.Event()
    _dialogue.start_plan_executor(
        execute_fn=_execute_dialogue_plan,
        cancel_event=_dialogue_executor_stop,
    )

    def _dialogue_context_worker() -> None:
        while not machine.is_shutting_down:
            time.sleep(_dialogue.idle_context_interval_seconds)
            if not _use_proactive:
                continue
            if not machine.can_start_conversation:
                continue
            event = _dialogue.build_context_event(reason="idle_context")
            if not event.extra_context:
                continue
            decision = _dialogue.decide(event)
            if decision.action in ("speak", "backchannel", "schedule"):
                if decision.context_use == "none":
                    continue
                if decision.action == "schedule":
                    _dialogue.add_plan(decision, created_from="idle_context")
                else:
                    _dialogue.add_plan(decision, created_from="idle_context_now")

    threading.Thread(target=_dialogue_context_worker, daemon=True).start()

    refine_url, refine_model, refine_key = refine_endpoint()

    device = args.device if args.device is not None else stt_mod.find_input_device()
    if device is None:
        print("\n[error] No microphone device found.")
        print("Run `python cli.py --list-devices` to inspect available devices.\n")
        return

    model_path = stt_mod.download_model(CONFIG.get("stt_model_dir", "models/stt"))
    print("  [cli] Loading speech model...")
    recognizer = stt_mod.create_recognizer(
        model_path,
        argparse.Namespace(num_threads=4, hotwords="", hotwords_score=1.5, verbose=False),
    )

    # ── ConversationManager (replaces old ConversationPool + manual STT loop) ──
    def _on_stt_partial(text: str) -> None:
        if _stt_subtitle_client:
            _stt_subtitle_client.push_text(text, mode="set")
        sys.stdout.write(f"\r\033[K  [STT] {text}")
        sys.stdout.flush()
        if machine.is_idle or machine.state == sm.SystemState.SCREEN_WATCHING:
            machine.emit(sm.SystemEvent.USER_SPEECH_START)
            machine.set_stt_state(sm.STTState.LISTENING)

    conversation = conversation_mod.ConversationManager(
        recognizer=recognizer,
        machine=machine,
        on_user_utterance=on_user_utterance,
        on_partial=_on_stt_partial,
        sample_rate=stt_mod.SAMPLE_RATE,
        silence_endpoint_delay=cfg.stt_refine_stable_seconds(),
        commit_delay=cfg.stt_utterance_commit_seconds(),
        short_extra_delay=cfg.stt_short_utterance_extra_seconds(),
        short_max_chars=cfg.stt_short_utterance_max_chars(),
    )

    print()
    print("=" * 50)
    print("  Alice CLI")
    print(f"  Character: {session.character_name}")
    print(f"  Dialogue model: {dialogue_model}")
    print(f"  Microphone: [{device}]")
    print(f"  TTS: {tts_engine is not None}")
    print(f"  Portrait: {portrait_worker is not None}")
    print(f"  VTS: {_vts_controller is not None}")
    print(f"  Proactive dialogue: {_use_proactive}")
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

    # ── STT worker — feeds AEC-cleaned audio into ConversationManager ──────
    pause_during_tts = cfg.stt_pause_during_tts()
    if _aec_processor is not None:
        pause_during_tts = False

    def stt_worker() -> None:
        import sounddevice as sd

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
                    continue

                if _aec_processor is not None:
                    mono = stt_mod.denoise(_aec_processor.process(chunk[:, 0]))
                else:
                    mono = stt_mod.denoise(chunk[:, 0])

                conversation.feed_audio(mono)
                _maybe_flush_user_turn()
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

            # Always update cache (dialogue orchestrator reads from here)
            sc.put(result)

            # Keep this as cache only. DialogueOrchestrator decides whether
            # cached screen content is worth discussing.
            if not machine.is_busy and result.score >= screen_interest_threshold:
                context = result.content or result.reason
                print(f"\n  [screen] cached interest={result.score:.1f} {context.split(chr(10))[0]}")

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

    # ── main loop ──────────────────────────────────────────────────────────
    try:
        while not machine.is_shutting_down:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n\n[cli] Stopping...")
    finally:
        machine.emit(sm.SystemEvent.SHUTDOWN)
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
        # ── VTS cleanup ──────────────────────────────────────────────────────────
        if _vts_idle_loop:
            try:
                asyncio.run_coroutine_threadsafe(_vts_idle_loop.stop(), _vts_loop).result(timeout=3)
            except Exception:
                pass
        if _vts_arbiter:
            try:
                asyncio.run_coroutine_threadsafe(_vts_arbiter.stop(), _vts_loop).result(timeout=3)
            except Exception:
                pass
        if _vts_controller:
            try:
                asyncio.run_coroutine_threadsafe(_vts_controller.close(), _vts_loop).result(timeout=3)
            except Exception:
                pass
        if _vts_loop:
            _vts_loop.call_soon_threadsafe(_vts_loop.stop)
        # ── Flush cached memory events to vector store ────────────────────────────
        if session is not None and hasattr(session, 'memory_events') and session.memory_events is not None:
            session.memory_events.flush_all(
                user_name=session.user_name,
                character_name=session.character_name,
                summary=session.summary or "",
            )



# ── multi-character chat ───────────────────────────────────────────────────


_tts_lock = threading.Lock()


def _tts_say(engine, text, *, wait: bool = True):
    """Push text to TTS engine. Uses global lock so character voices do not overlap."""
    if engine is None:
        return
    with _tts_lock:
        try:
            if not engine.prepare():
                return
            for line in text.splitlines():
                line = line.strip()
                if line:
                    engine.push(line)
            engine.end_sentence()
            if wait:
                while getattr(engine, "is_playing", False):
                    time.sleep(0.05)
        except Exception as exc:
            print("  [tts] error: " + str(exc))


def _wait_for_multi_tts(tts_map: dict[str, object], cancel_event: threading.Event | None = None) -> None:
    while any(getattr(engine, "is_playing", False) for engine in tts_map.values()):
        if cancel_event and cancel_event.is_set():
            break
        time.sleep(0.05)


def _run_multi_cli(args):
    """Multi-character chat with TTS + portrait."""
    cids = [c.strip() for c in args.multi.split(",") if c.strip()]
    if len(cids) < 2:
        print("[error] --multi needs at least 2 character IDs")
        return

    from kokoro import character as char_mod
    from kokoro import config as cfg
    from kokoro import memory as mem_mod

    runtime_cfg = cfg.load()
    user_name = cfg.user_name()
    default_model = cfg.llm_model()
    machine = sm.SystemStateMachine()
    machine.emit(sm.SystemEvent.INIT_DONE)

    io_lock = threading.Lock()
    speech_gate_lock = threading.Lock()
    speech_gate_until = {"value": 0.0}

    def safe_print(*parts, sep=" ", end="\n"):
        with io_lock:
            print(*parts, sep=sep, end=end, flush=True)

    def hold_auto_turns(seconds: float) -> None:
        until = time.monotonic() + max(0.0, float(seconds))
        with speech_gate_lock:
            speech_gate_until["value"] = max(speech_gate_until["value"], until)

    def auto_turns_blocked() -> bool:
        with speech_gate_lock:
            return time.monotonic() < speech_gate_until["value"]

    tts_map = {}
    all_chars = char_mod.load()
    for cid in cids:
        char_data = all_chars.get(cid, {})
        voice_id = char_data.get("tts_voice_id")
        if not args.no_tts:
            try:
                tts_mod.warmup()
                eng = tts_mod.StreamingTTS(voice=voice_id)
                eng.prepare()
                tts_map[cid] = eng
            except Exception as exc:
                safe_print("  [tts] init failed for " + cid + ": " + str(exc))

    _aec_processor = None
    if cfg.aec_enabled() and tts_map:
        try:
            from kokoro.aec import AECProcessor

            tts_sr = tts_mod.SAMPLE_RATE
            _aec_processor = AECProcessor(
                mic_sample_rate=stt_mod.SAMPLE_RATE,
                tts_sample_rate=tts_sr,
                ns_level=cfg.aec_ns_level(),
            )
            _aec_processor.set_delay(cfg.aec_delay_ms())
            for engine in tts_map.values():
                engine.on_audio_frame = _aec_processor.push_reference
            safe_print(
                f"  [aec] enabled (playback_sr={tts_sr}, mic_sr={stt_mod.SAMPLE_RATE}, delay={cfg.aec_delay_ms()}ms)"
            )
        except Exception as exc:
            safe_print(f"  [aec] init failed: {exc}")
            _aec_processor = None

    model = args.model or default_model
    if "charglm" in model:
        model = default_model

    cfg_inst = multi_chat_mod.MultiChatConfig(character_ids=cids, model=model)
    orch = multi_chat_mod.MultiChatOrchestrator(cfg_inst)
    names = orch.character_names

    portrait_clients = {}
    portrait_workers = {}
    if not args.no_portrait:
        base_port = int(runtime_cfg.get("portrait_overlay_port", 17352)) + 1
        for idx, cid in enumerate(cids):
            try:
                client, worker = portrait_controller.create_controller(
                    cid,
                    model,
                    port=base_port + idx,
                    slot_index=idx,
                    slot_count=len(cids),
                    state_file="portrait_overlay_state_" + cid + ".json",
                )
                portrait_clients[cid] = client
                portrait_workers[cid] = worker
            except Exception as exc:
                safe_print("  [portrait] init failed for " + cid + ": " + str(exc))

    print("=" * 50)
    print("  Multi-Character Chat")
    for cid, cname in names.items():
        tts_on = "on" if tts_map.get(cid) else "off"
        print("  " + cid + " -> " + cname + "  [tts:" + tts_on + "]")
    print("  User: " + user_name)
    print("  Voice input: on")
    print("  AEC: " + ("enabled" if _aec_processor is not None else "disabled"))
    if args.watch:
        print("  Mode: watch (unattended)")
        print("  Stop: Ctrl+C")
    else:
        print("  Commands: /exit, /auto N, /watch [N]")
        print("  Empty input = auto next turn")
    print("=" * 50)

    predicted_lock = threading.Lock()
    predicted_turn: dict[str, object | None] = {"value": None}
    predicted_thread: dict[str, threading.Thread | None] = {"value": None}
    predicted_serial = {"value": 0}
    recent_tts_texts: deque[tuple[float, str]] = deque(maxlen=12)
    recent_tts_lock = threading.Lock()

    def remember_tts_text(text: str) -> None:
        norm = _normalize_echo_text(text)
        if not norm:
            return
        now = time.monotonic()
        with recent_tts_lock:
            recent_tts_texts.append((now, norm))
            while recent_tts_texts and now - recent_tts_texts[0][0] > 8.0:
                recent_tts_texts.popleft()

    def is_probable_tts_echo(text: str) -> bool:
        norm = _normalize_echo_text(text)
        if len(norm) < 8:
            return False
        now = time.monotonic()
        with recent_tts_lock:
            while recent_tts_texts and now - recent_tts_texts[0][0] > 8.0:
                recent_tts_texts.popleft()
            for _, spoken in recent_tts_texts:
                if norm in spoken or spoken in norm:
                    return True
        return False

    def clear_prediction() -> None:
        with predicted_lock:
            predicted_serial["value"] += 1
            predicted_turn["value"] = None
            predicted_thread["value"] = None

    def start_prediction(cid: str, cname: str, reply: str) -> None:
        if not args.watch or not reply:
            return
        with predicted_lock:
            predicted_serial["value"] += 1
            serial = predicted_serial["value"]

        def worker() -> None:
            try:
                prepared = orch.prepare_followup_turn(cid, cname, reply)
            except Exception as exc:
                safe_print("  [multi-dialogue] prefetch failed: " + str(exc))
                prepared = None
            with predicted_lock:
                if serial == predicted_serial["value"]:
                    predicted_turn["value"] = prepared

        thread = threading.Thread(target=worker, daemon=True)
        with predicted_lock:
            predicted_turn["value"] = None
            predicted_thread["value"] = thread
        thread.start()

    def take_prediction(timeout: float = 0.1):
        thread = predicted_thread.get("value")
        if thread:
            thread.join(timeout=max(0.0, timeout))
            if thread.is_alive():
                return "", "", ""
        with predicted_lock:
            prepared = predicted_turn["value"]
            predicted_turn["value"] = None
            predicted_thread["value"] = None
        return orch.commit_prepared_turn(prepared)

    def do_turn(cid, cname, reply, *, prefetch: bool = False):
        if not reply:
            return
        safe_print()
        safe_print(cname + "> " + reply)
        remember_tts_text(reply)
        portrait_worker = portrait_workers.get(cid)
        if portrait_worker:
            portrait_worker.submit("", reply)
        engine = tts_map.get(cid)
        if engine:
            _tts_say(engine, reply, wait=True)
        if prefetch:
            start_prediction(cid, cname, reply)

    def handle_user_text(text: str, *, prefetch: bool | None = None):
        if not text:
            return
        hold_auto_turns(1.2)
        clear_prediction()
        safe_print()
        safe_print(user_name + "> " + text)
        for cid, cname, reply in orch.user_turn(text):
            do_turn(cid, cname, reply, prefetch=args.watch if prefetch is None else prefetch)

    _pending_user_turn = {"text": "", "deadline": 0.0}
    _pending_user_lock = threading.Lock()

    def maybe_flush_user_turn(force: bool = False) -> None:
        with _pending_user_lock:
            deadline = float(_pending_user_turn.get("deadline", 0.0) or 0.0)
            if not force and (not deadline or time.monotonic() < deadline):
                return
            text = _pending_user_turn["text"].strip()
            _pending_user_turn["text"] = ""
            _pending_user_turn["deadline"] = 0.0
        if not text:
            return
        machine.emit(sm.SystemEvent.STT_REFINED)
        hold_auto_turns(2.0)
        if _aec_processor is not None:
            _aec_processor.reset()
        handle_user_text(text, prefetch=args.watch)
        machine.set_stt_state(sm.STTState.LISTENING)

    def on_user_utterance(text: str) -> None:
        if is_probable_tts_echo(text):
            conversation.reset_stream()
            safe_print("\n  [stt] dropped probable tts echo")
            machine.set_stt_state(sm.STTState.LISTENING)
            return
        with _pending_user_lock:
            _pending_user_turn["text"] = merge_stt_text(_pending_user_turn["text"], text)
            _pending_user_turn["deadline"] = time.monotonic() + stt_turn_deadline_delay(_pending_user_turn["text"])

    def _on_stt_partial(text: str) -> None:
        with io_lock:
            sys.stdout.write(f"\r\033[K  [STT] {text}")
            sys.stdout.flush()
        hold_auto_turns(0.8)
        if machine.is_idle:
            machine.emit(sm.SystemEvent.USER_SPEECH_START)
            machine.set_stt_state(sm.STTState.LISTENING)

    device = args.device if args.device is not None else stt_mod.find_input_device()
    if device is None:
        safe_print("\n[error] No microphone device found.")
        safe_print("Run `python cli.py --list-devices` to inspect available devices.\n")
        return

    model_path = stt_mod.download_model(CONFIG.get("stt_model_dir", "models/stt"))
    safe_print("  [cli] Loading speech model...")
    recognizer = stt_mod.create_recognizer(
        model_path,
        argparse.Namespace(num_threads=4, hotwords="", hotwords_score=1.5, verbose=False),
    )
    conversation = conversation_mod.ConversationManager(
        recognizer=recognizer,
        machine=machine,
        on_user_utterance=on_user_utterance,
        on_partial=_on_stt_partial,
        sample_rate=stt_mod.SAMPLE_RATE,
        silence_endpoint_delay=cfg.stt_refine_stable_seconds(),
        commit_delay=cfg.stt_utterance_commit_seconds(),
        short_extra_delay=cfg.stt_short_utterance_extra_seconds(),
        short_max_chars=cfg.stt_short_utterance_max_chars(),
    )

    # In multi-character voice chat, pausing STT during TTS makes the user
    # effectively unable to break in while either character is speaking.
    # Keep STT live here; echo handling should be solved separately by AEC.
    pause_during_tts = False

    def stt_worker() -> None:
        import sounddevice as sd

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
                try:
                    chunk, _ = audio_stream.read(1600)
                except Exception as exc:
                    if machine.is_shutting_down or "Invalid stream pointer" in str(exc):
                        break
                    raise

                if pause_during_tts and any(engine and engine.is_playing for engine in tts_map.values()):
                    continue

                if _aec_processor is not None:
                    mono = stt_mod.denoise(_aec_processor.process(chunk[:, 0]))
                else:
                    mono = stt_mod.denoise(chunk[:, 0])
                conversation.feed_audio(mono)
                maybe_flush_user_turn()
        except Exception as exc:
            safe_print(f"\n[STT error] {exc}")
            traceback.print_exc()
            machine.emit_error("stt_multi")
        finally:
            if audio_stream is not None:
                try:
                    audio_stream.stop()
                    audio_stream.close()
                except Exception:
                    pass

    stt_thread = threading.Thread(target=stt_worker, daemon=True)
    stt_thread.start()

    def run_auto_turns(limit: int, *, sleep_between: bool = False) -> int:
        produced = 0
        while limit <= 0 or produced < limit:
            if sleep_between and produced > 0:
                time.sleep(max(0.1, float(args.idle_seconds)))
            if machine.is_listening or auto_turns_blocked():
                time.sleep(0.1)
                continue
            _wait_for_multi_tts(tts_map)
            page_changed = bool(getattr(orch, "consume_random_mc_page_change", lambda: False)())
            if page_changed:
                clear_prediction()
                cid, cname, reply = orch.auto_turn()
            else:
                cid, cname, reply = take_prediction(timeout=0.5)
                if not reply:
                    cid, cname, reply = orch.auto_turn()
            if not reply and getattr(orch, "last_auto_action", "") in ("silence", "observe", "cancel_plan"):
                clear_prediction()
            elif not reply:
                cid, cname, reply = take_prediction()
            if not reply:
                if limit > 0:
                    break
                time.sleep(max(0.5, float(args.idle_seconds)))
                continue
            do_turn(cid, cname, reply, prefetch=True)
            produced += 1
        return produced

    if args.auto > 0:
        print()
        print("--- Auto " + str(args.auto) + " rounds ---")
        opening = args.topic
        if opening:
            handle_user_text(opening, prefetch=args.watch)
        for cid, cname, reply in orch.auto_cycle(rounds=args.auto):
            do_turn(cid, cname, reply, prefetch=args.watch)

    try:
        try:
            if args.watch:
                print()
                print("--- Watch mode ---")
                if not args.auto:
                    opening = args.topic or "我们一起随便聊聊吧，你们两个也别太晾着我。"
                    handle_user_text(opening, prefetch=True)
                run_auto_turns(max(0, int(args.max_turns)), sleep_between=True)
                return

            while True:
                try:
                    raw = input(chr(10) + "[" + user_name + "] (enter=auto) > ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not raw:
                    clear_prediction()
                    cid, cname, reply = orch.auto_turn()
                    do_turn(cid, cname, reply)
                    continue
                if raw in ("/exit", "/quit"):
                    break
                if raw.startswith("/auto "):
                    try:
                        n = int(raw.split("/auto ", 1)[1])
                    except (ValueError, IndexError):
                        n = 3
                    print()
                    print("--- Auto " + str(n) + " rounds ---")
                    clear_prediction()
                    for cid, cname, reply in orch.auto_cycle(rounds=n):
                        do_turn(cid, cname, reply)
                    continue
                if raw.startswith("/watch"):
                    parts = raw.split()
                    try:
                        n = int(parts[1]) if len(parts) > 1 else 0
                    except ValueError:
                        n = 0
                    print()
                    print("--- Watch mode " + ("unlimited" if n <= 0 else str(n) + " turns") + " ---")
                    clear_prediction()
                    run_auto_turns(n, sleep_between=True)
                    continue
                handle_user_text(raw, prefetch=False)
        except KeyboardInterrupt:
            pass
    finally:
        try:
            machine.emit(sm.SystemEvent.SHUTDOWN)
        except Exception:
            pass
        for engine in tts_map.values():
            try:
                engine.close()
            except Exception:
                pass
        for worker in portrait_workers.values():
            worker.stop()
        for client in portrait_clients.values():
            client.shutdown()
        try:
            orch.close()
        except Exception:
            pass



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
