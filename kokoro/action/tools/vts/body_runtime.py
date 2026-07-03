"""Runtime class for LLM-scripted VTS face/body motion."""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from kokoro.core import config as cfg
from kokoro.core import prompts
from kokoro.action.tools.vts.body_motion import _clamp_params, _motion_to_params, _smooth_params
from kokoro.action.tools.vts.body_scripts import (
    MotionScript,
    _direct_motion_scripts,
    _fallback_body_script,
    _fallback_face_script,
    _idle_face_life_script,
    _sanitize_idle_body_script,
    _script_from_data,
)
from kokoro.action.tools.vts.body_utils import _clamp, _extract_json, _safe_context
from kokoro.action.tools.vts.controller_arbiter import VTSExpressionArbiter

logger = logging.getLogger(__name__)

FACE_LAYER = "face_script"
BODY_LAYER = "body_script"
DIRECT_FACE_LAYER = "direct_face"
DIRECT_BODY_LAYER = "direct_body"


class VTSBodyDriver:
    """Runs separate LLM-authored face/body scripts through smooth local curves."""

    def __init__(
        self,
        *,
        arbiter: VTSExpressionArbiter,
        session,
        update_hz: float = 30.0,
        intent_interval_seconds: float = 2.0,
        idle_request_seconds: float = 2.5,
        model: str = "",
        enabled: bool = True,
        debug_log: bool = True,
    ) -> None:
        self.arbiter = arbiter
        self.controller = arbiter.controller
        self.session = session
        self.enabled = bool(enabled)
        self.update_hz = max(5.0, float(update_hz))
        self.intent_interval_seconds = max(0.5, float(intent_interval_seconds))
        self.idle_request_seconds = max(0.5, float(idle_request_seconds))
        self.model = model or cfg.llm_model()
        self.debug_log = bool(debug_log)

        self._running = False
        self._task: asyncio.Task | None = None
        self._lock = threading.Lock()
        self._face_script = _fallback_face_script("安静待机")
        self._body_script = _fallback_body_script("安静待机")
        self._target_face: dict[str, float] = {}
        self._target_body: dict[str, float] = {}
        self._current_face: dict[str, float] = {}
        self._current_body: dict[str, float] = {}
        self._last_request_at = 0.0
        self._last_event_text = ""
        self._speaking = False
        self._recent_scripts: list[str] = []
        self._last_debug_print = 0.0
        self._param_cache_logged = False
        self._direct_face_script: MotionScript | None = None
        self._direct_body_script: MotionScript | None = None
        self._current_direct_face: dict[str, float] = {}
        self._current_direct_body: dict[str, float] = {}

    async def start(self) -> None:
        if self._running or not self.enabled:
            return
        self._running = True
        try:
            names = await self.controller.refresh_parameter_cache()
            interesting = sorted(n for n in names if any(x in n for x in ("FaceAngle", "FacePosition", "MocopiBody", "Mouth")))
            print(f"  [vts] parameters: {', '.join(interesting[:32])}")
        except Exception as exc:
            print(f"  [vts] parameter scan failed: {type(exc).__name__}: {exc}")
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.arbiter.clear_layer(FACE_LAYER)
        self.arbiter.clear_layer(BODY_LAYER)
        self.arbiter.clear_layer(DIRECT_FACE_LAYER)
        self.arbiter.clear_layer(DIRECT_BODY_LAYER)

    def set_speaking(self, active: bool) -> None:
        self._speaking = bool(active)

    def request_update(self, reason: str = "state_update", event_text: str = "") -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        if now - self._last_request_at < self.intent_interval_seconds:
            return
        self._last_request_at = now
        self._last_event_text = str(event_text or reason or "")[-800:]
        threading.Thread(target=self._request_script_worker, args=(reason,), daemon=True).start()

    def play_direct_motion(
        self,
        motion: str,
        *,
        intensity: float = 0.75,
        duration: float = 4.0,
        reason: str = "",
    ) -> None:
        intensity = _clamp(float(intensity), 0.0, 1.0)
        duration = _clamp(float(duration), 0.5, 12.0)
        face, body = _direct_motion_scripts(motion, intensity=intensity, duration=duration, reason=reason)
        with self._lock:
            self._direct_face_script = face
            self._direct_body_script = body
            self._current_direct_face = {}
            self._current_direct_body = {}
            summary = f"direct motion={motion} intensity={intensity:.2f} duration={duration:.1f}s reason={reason or '-'}"
            self._recent_scripts.append(summary)
            self._recent_scripts = self._recent_scripts[-8:]
        print(f"  [vts_body] {summary}")
        logger.info("[vts_body] %s", summary)

    async def _loop(self) -> None:
        period = 1.0 / self.update_hz
        while self._running:
            now = time.monotonic()
            with self._lock:
                face = self._evaluate_script(self._face_script, now, channel="face")
                body = self._evaluate_script(self._body_script, now, channel="body")
                direct_face = self._evaluate_script(self._direct_face_script, now, channel="face") if self._direct_face_script else {}
                direct_body = self._evaluate_script(self._direct_body_script, now, channel="body") if self._direct_body_script else {}
                if now > self._face_script.end_at:
                    self._face_script = _fallback_face_script("自然待机", energy=self._face_script.energy)
                if now > self._body_script.end_at:
                    self._body_script = _fallback_body_script("自然待机", energy=self._body_script.energy)
                if self._direct_face_script and now > self._direct_face_script.end_at:
                    self._direct_face_script = None
                    direct_face = {}
                if self._direct_body_script and now > self._direct_body_script.end_at:
                    self._direct_body_script = None
                    direct_body = {}
            self._target_face = face
            self._target_body = body
            self._current_face = _smooth_params(self._current_face, self._target_face, alpha=0.14)
            self._current_body = _smooth_params(self._current_body, self._target_body, alpha=0.11)
            self._current_direct_face = _smooth_params(self._current_direct_face, direct_face, alpha=0.34)
            self._current_direct_body = _smooth_params(self._current_direct_body, direct_body, alpha=0.42)
            if self._current_face:
                self.arbiter.set_layer(FACE_LAYER, self._current_face)
            if self._current_body:
                self.arbiter.set_layer(BODY_LAYER, self._current_body)
            if self._current_direct_face:
                self.arbiter.set_layer(DIRECT_FACE_LAYER, self._current_direct_face)
            elif not self._direct_face_script:
                self.arbiter.clear_layer(DIRECT_FACE_LAYER)
            if self._current_direct_body:
                self.arbiter.set_layer(DIRECT_BODY_LAYER, self._current_direct_body)
            elif not self._direct_body_script:
                self.arbiter.clear_layer(DIRECT_BODY_LAYER)
            if self.debug_log and now - self._last_debug_print > 5.0:
                self._last_debug_print = now
                preview_source = self._current_direct_body or self._current_body
                body_preview = ", ".join(f"{k}={v:.2f}" for k, v in sorted(preview_source.items()))
                if body_preview:
                    print(f"  [vts_body] params {body_preview}")
                if not self._param_cache_logged:
                    self._param_cache_logged = True
                    available = sorted(
                        name for name in getattr(self.controller, "_available_parameter_names", set())
                        if any(x in name for x in ("FaceAngle", "FacePosition", "MocopiBody"))
                    )
                    if available:
                        print(f"  [vts_body] available body params: {', '.join(available)}")
            if now - self._last_request_at > self.idle_request_seconds:
                self.request_update("idle_body_life", self._last_event_text)
            await asyncio.sleep(period)

    def _request_script_worker(self, reason: str) -> None:
        try:
            data = self._call_script_llm(reason)
            face = _script_from_data(data.get("face"), channel="face") if isinstance(data, dict) else None
            body = _script_from_data(data.get("body"), channel="body") if isinstance(data, dict) else None
            if reason == "idle_body_life":
                # Idle should feel alive, not emotionally unstable. Keep the
                # face on a calm baseline and let the body carry the cuteness.
                face = _idle_face_life_script()
                body = _sanitize_idle_body_script(body)
            with self._lock:
                if face:
                    self._face_script = face
                if body:
                    self._body_script = body
                if self.debug_log:
                    summary = f"face={face.mood if face else '-'} body={body.mood if body else '-'} reason={reason}"
                    self._recent_scripts.append(summary)
                    self._recent_scripts = self._recent_scripts[-8:]
                    print(f"  [vts_body] {summary}")
                    logger.info("[vts_body] %s", summary)
        except Exception as exc:
            logger.debug("vts body script failed: %s", exc)

    def _call_script_llm(self, reason: str) -> dict:
        prompt = prompts.format_prompt(
            "vts_body.script_user",
            name=getattr(self.session, "character_name", "角色"),
            reason=reason,
            speaking="是" if self._speaking else "否",
            inner_stream=_safe_context(getattr(self.session, "inner_stream", None)) or "无",
            emotion_context=_safe_context(getattr(self.session, "emotion", None)) or "无",
            cognition_context=_safe_context(getattr(self.session, "cognition", None)) or "无",
            recent_history=getattr(self.session, "recent_history_text", lambda n=8: "")(8) or "无",
            event_text=self._last_event_text or "无",
            recent_scripts="\n".join(self._recent_scripts) or "无",
        )
        system_prompt = prompts.format_prompt(
            "vts_body.script_system",
            name=getattr(self.session, "character_name", "角色"),
        )
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        from kokoro.core import deepseek_api

        try:
            result = deepseek_api.chat(
                messages,
                model=self.model,
                temperature=0.6,
                max_tokens=800,
                json_mode=True,
                function="vts_body_script",
                timeout=20,
            )
            raw = result["content"]
        except Exception:
            return {}
        return _extract_json(raw) or {}

    def _evaluate_script(self, script: MotionScript, now: float, *, channel: str) -> dict[str, float]:
        elapsed = max(0.0, now - script.created_at)
        duration = max(0.5, script.duration)
        envelope = _envelope(elapsed, duration, hold_tail=(script.channel == "body"))
        params: dict[str, float] = {}
        for motion in script.motions:
            params.update(_motion_to_params(motion, elapsed, envelope, channel=channel, energy=script.energy))
        return _clamp_params(params)
