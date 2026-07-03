"""VTube Studio runtime wiring.

This module keeps VTS as an action/tool capability instead of embedding its
connection lifecycle in the CLI entrypoint.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Any

from kokoro.action.tools.vts import controller as vts_mod
from kokoro.action.tools.vts.body_driver import VTSBodyDriver


@dataclass
class VTSRuntime:
    controller: vts_mod.VTSController | None = None
    arbiter: vts_mod.VTSExpressionArbiter | None = None
    idle_loop: vts_mod.VTSIdleLoop | None = None
    lipsync: vts_mod.VTSLipSync | None = None
    body_driver: VTSBodyDriver | None = None
    loop: asyncio.AbstractEventLoop | None = None
    loop_thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return self.controller is not None

    def say_resource_kwargs(self) -> dict[str, object | None]:
        return {
            "vts_controller": self.controller,
            "vts_arbiter": self.arbiter,
            "vts_body_driver": self.body_driver,
            "event_loop": self.loop,
        }

    def shutdown(self) -> None:
        if self.idle_loop and self.loop:
            try:
                asyncio.run_coroutine_threadsafe(self.idle_loop.stop(), self.loop).result(timeout=3)
            except Exception:
                pass
        if self.body_driver and self.loop:
            try:
                asyncio.run_coroutine_threadsafe(self.body_driver.stop(), self.loop).result(timeout=3)
            except Exception:
                pass
        if self.arbiter and self.loop:
            try:
                asyncio.run_coroutine_threadsafe(self.arbiter.stop(), self.loop).result(timeout=3)
            except Exception:
                pass
        if self.controller and self.loop:
            try:
                asyncio.run_coroutine_threadsafe(self.controller.close(), self.loop).result(timeout=3)
            except Exception:
                pass
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)


def start(
    *,
    config: dict,
    character_id: str,
    session: Any,
    tts_engine: Any,
    machine: Any,
) -> VTSRuntime:
    runtime = VTSRuntime()
    vts_cfg = config.get("vts", {})
    if not isinstance(vts_cfg, dict) or not vts_cfg.get("enabled", True):
        return runtime

    try:
        runtime.controller = vts_mod.VTSController(
            host=str(vts_cfg.get("host", "localhost")),
            port=int(vts_cfg.get("port", 8001)),
            character_id=character_id,
        )
        runtime.loop = asyncio.new_event_loop()
        runtime.loop_thread = threading.Thread(target=runtime.loop.run_forever, daemon=True)
        runtime.loop_thread.start()

        auth_future = asyncio.run_coroutine_threadsafe(runtime.controller.authenticate(), runtime.loop)
        auth_future.result(timeout=10)

        runtime.arbiter = vts_mod.VTSExpressionArbiter(runtime.controller)
        asyncio.run_coroutine_threadsafe(runtime.arbiter.start(), runtime.loop)

        runtime.idle_loop = vts_mod.VTSIdleLoop(runtime.arbiter)
        asyncio.run_coroutine_threadsafe(runtime.idle_loop.start(), runtime.loop)

        runtime.lipsync = vts_mod.VTSLipSync(runtime.controller, runtime.arbiter, loop=runtime.loop)
        body_cfg = vts_cfg.get("body", {}) if isinstance(vts_cfg.get("body", {}), dict) else {}
        runtime.body_driver = VTSBodyDriver(
            arbiter=runtime.arbiter,
            session=session,
            enabled=bool(body_cfg.get("enabled", True)),
            update_hz=float(body_cfg.get("update_hz", 30.0)),
            intent_interval_seconds=float(body_cfg.get("intent_interval_seconds", 2.0)),
            idle_request_seconds=float(body_cfg.get("idle_request_seconds", 2.5)),
            model=str(body_cfg.get("model", "") or ""),
            debug_log=bool(body_cfg.get("debug_log", True)),
        )
        asyncio.run_coroutine_threadsafe(runtime.body_driver.start(), runtime.loop)

        if tts_engine is not None:
            original_audio_frame = tts_engine.on_audio_frame

            def _vts_audio_wrapper(chunk):
                if runtime.lipsync:
                    runtime.lipsync.on_audio_frame(chunk)
                if original_audio_frame:
                    original_audio_frame(chunk)

            tts_engine.on_audio_frame = _vts_audio_wrapper

        def _on_vts_emotion(tone: str, motivation: str) -> None:
            if runtime.arbiter is None:
                return
            runtime.arbiter.clear_layer("emotion")
            if runtime.body_driver is not None:
                runtime.body_driver.request_update(
                    "emotion_update",
                    f"情绪基调：{tone}\n中期动机：{motivation}",
                )

        if session is not None and hasattr(session, "emotion"):
            session.emotion._on_update = _on_vts_emotion

        _start_tts_monitor(runtime, tts_engine=tts_engine, machine=machine)
        print("  [vts] VTube Studio connected")
    except Exception as exc:
        print(f"  [vts] Init failed: {exc}")
        runtime.shutdown()
        return VTSRuntime()
    return runtime


def _start_tts_monitor(runtime: VTSRuntime, *, tts_engine: Any, machine: Any) -> None:
    if runtime.idle_loop is None:
        return

    def _tts_state_monitor() -> None:
        was_active = False
        while not machine.is_shutting_down and runtime.idle_loop:
            is_active = bool(tts_engine and tts_engine.is_playing)
            runtime.idle_loop.set_tts_active(is_active)
            if runtime.body_driver is not None:
                runtime.body_driver.set_speaking(is_active)
            if is_active and not was_active:
                if runtime.lipsync:
                    runtime.lipsync.start()
                if runtime.body_driver is not None:
                    runtime.body_driver.request_update("tts_started", "开始说话")
            elif not is_active and was_active:
                if runtime.lipsync:
                    runtime.lipsync.stop()
                if runtime.body_driver is not None:
                    runtime.body_driver.request_update("tts_finished", "刚说完话")
            was_active = is_active
            time.sleep(0.5)

    threading.Thread(target=_tts_state_monitor, daemon=True).start()
