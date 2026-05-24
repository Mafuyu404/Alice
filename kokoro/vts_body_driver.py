"""LLM-scripted Live2D face/body motion driver for VTube Studio."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from kokoro import config as cfg
from kokoro import prompts
from kokoro import token_usage
from kokoro.vts_controller import (
    PARAM_BROWS,
    PARAM_EYE_LEFT_X,
    PARAM_EYE_LEFT_Y,
    PARAM_EYE_OPEN_L,
    PARAM_EYE_OPEN_R,
    PARAM_EYE_RIGHT_X,
    PARAM_EYE_RIGHT_Y,
    PARAM_FACE_ANGLE_X,
    PARAM_FACE_ANGLE_Y,
    PARAM_FACE_ANGLE_Z,
    PARAM_FACE_POS_X,
    PARAM_FACE_POS_Y,
    PARAM_FACE_POS_Z,
    PARAM_MOCOPI_BODY_ANGLE_X,
    PARAM_MOCOPI_BODY_ANGLE_Y,
    PARAM_MOCOPI_BODY_ANGLE_Z,
    PARAM_MOCOPI_BODY_POS_X,
    PARAM_MOCOPI_BODY_POS_Y,
    PARAM_MOCOPI_BODY_POS_Z,
    PARAM_MOUTH_OPEN,
    PARAM_MOUTH_SMILE,
    VTSExpressionArbiter,
)

logger = logging.getLogger(__name__)


FACE_LAYER = "face_script"
BODY_LAYER = "body_script"
DIRECT_FACE_LAYER = "direct_face"
DIRECT_BODY_LAYER = "direct_body"


@dataclass
class MotionScript:
    channel: str
    mood: str = ""
    energy: float = 0.35
    duration: float = 3.0
    motions: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    created_at: float = field(default_factory=time.monotonic)

    @property
    def end_at(self) -> float:
        return self.created_at + max(0.5, self.duration)


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

        model = self.model
        headers = {"Content-Type": "application/json"}
        url = cfg.llm_url()
        openai_compatible = False
        if cfg.is_deepseek_model(model):
            openai_compatible = True
            url = cfg.deepseek_url()
            headers["Authorization"] = f"Bearer {cfg.deepseek_api_key()}"

        if openai_compatible:
            base_url = url.rstrip("/")
            if not re.search(r"/v\d+$", base_url):
                base_url += "/v1"
            api_url = f"{base_url}/chat/completions"
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.6,
                "max_tokens": 800,
                "response_format": {"type": "json_object"},
            }
        else:
            api_url = f"{url.rstrip('/')}/api/chat"
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.6, "num_predict": 800},
            }
        req = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read())
        if openai_compatible:
            usage = result.get("usage", {})
            token_usage.record(model, "vts_body_script", int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0))
            raw = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        else:
            token_usage.record(model, "vts_body_script", int(result.get("prompt_eval_count", 0) or 0), int(result.get("eval_count", 0) or 0))
            raw = result.get("message", {}).get("content", "")
        return _extract_json(raw) or {}

    def _evaluate_script(self, script: MotionScript, now: float, *, channel: str) -> dict[str, float]:
        elapsed = max(0.0, now - script.created_at)
        duration = max(0.5, script.duration)
        envelope = _envelope(elapsed, duration, hold_tail=(script.channel == "body"))
        params: dict[str, float] = {}
        for motion in script.motions:
            params.update(_motion_to_params(motion, elapsed, envelope, channel=channel, energy=script.energy))
        return _clamp_params(params)


def _motion_to_params(motion: dict[str, Any], t: float, envelope: float, *, channel: str, energy: float) -> dict[str, float]:
    target = str(motion.get("target") or "").lower()
    kind = str(motion.get("kind") or "").lower()
    raw_amp = _float(motion.get("amplitude"), 1.0)
    amp = raw_amp * max(0.1, energy) * envelope
    freq = max(0.05, _float(motion.get("frequency"), 0.5))
    phase = _float(motion.get("phase"), 0.0)
    wave = math.sin((t * freq + phase) * math.tau)
    value = _float(motion.get("value"), 0.0)
    out: dict[str, float] = {}

    if channel == "body":
        amp = raw_amp * (0.75 + min(1.0, max(0.0, energy)) * 0.45) * envelope
        if target == "head" and kind in {"sway", "shake"}:
            axis = str(motion.get("axis") or "x").lower()
            param = PARAM_FACE_ANGLE_X if axis == "x" else PARAM_FACE_ANGLE_Y if axis == "y" else PARAM_FACE_ANGLE_Z
            out[param] = wave * amp
            if axis == "x":
                out[PARAM_MOCOPI_BODY_ANGLE_X] = wave * amp * 0.7
                out[PARAM_MOCOPI_BODY_POS_X] = wave * min(1.0, amp * 0.11)
            elif axis == "y":
                out[PARAM_MOCOPI_BODY_ANGLE_Y] = wave * amp * 0.45
                out[PARAM_MOCOPI_BODY_POS_Y] = wave * min(0.8, amp * 0.06)
            else:
                out[PARAM_MOCOPI_BODY_ANGLE_Z] = wave * amp * 0.9
        elif target == "head" and kind == "nod":
            out[PARAM_FACE_ANGLE_Y] = wave * amp
            out[PARAM_MOCOPI_BODY_ANGLE_Y] = wave * amp * 0.55
            out[PARAM_MOCOPI_BODY_POS_Y] = wave * min(0.8, amp * 0.06)
        elif target == "head" and kind in {"tilt", "droop"}:
            out[PARAM_FACE_ANGLE_Z] = value * envelope if value else amp
            out[PARAM_MOCOPI_BODY_ANGLE_Z] = (value * 0.6 if value else amp * 0.6) * envelope
            if kind == "droop":
                out[PARAM_FACE_ANGLE_Y] = -abs(amp)
                out[PARAM_MOCOPI_BODY_ANGLE_Y] = -abs(amp) * 0.45
        elif target == "body" and kind in {"breath", "bounce"}:
            vertical = wave * min(1.8, amp * 0.45)
            out[PARAM_FACE_POS_Y] = vertical
            out[PARAM_MOCOPI_BODY_POS_Y] = wave * min(0.9, amp * 0.22)
        return out

    if channel == "face":
        if target == "mouth" and kind in {"smile", "pout"}:
            out[PARAM_MOUTH_SMILE] = (value if value else amp) * (1 if kind == "smile" else -1)
        elif target == "mouth" and kind == "open":
            out[PARAM_MOUTH_OPEN] = abs(value if value else amp) * envelope
        elif target == "eyes" and kind in {"squint", "sleepy"}:
            openness = max(0.15, 1.0 - abs(value if value else amp))
            out[PARAM_EYE_OPEN_L] = openness
            out[PARAM_EYE_OPEN_R] = openness
        elif target == "eyes" and kind == "look":
            x = _float(motion.get("x"), wave * amp * 0.15)
            y = _float(motion.get("y"), 0.0)
            out[PARAM_EYE_LEFT_X] = x * envelope
            out[PARAM_EYE_RIGHT_X] = x * envelope
            out[PARAM_EYE_LEFT_Y] = y * envelope
            out[PARAM_EYE_RIGHT_Y] = y * envelope
        elif target == "eyes" and kind in {"blink", "soft_blink"}:
            interval = max(1.0, _float(motion.get("interval"), 3.0))
            blink_phase = (t + phase) % interval
            if blink_phase < 0.08:
                out[PARAM_EYE_OPEN_L] = 0.0
                out[PARAM_EYE_OPEN_R] = 0.0
        elif target == "brows" and kind in {"raise", "frown"}:
            out[PARAM_BROWS] = (value if value else amp) * (1 if kind == "frown" else -1)
    return out


def _script_from_data(data: Any, *, channel: str) -> MotionScript | None:
    if not isinstance(data, dict):
        return None
    motions = data.get("motions")
    if not isinstance(motions, list):
        motions = []
    safe_motions = [m for m in motions if isinstance(m, dict)][:8]
    if not safe_motions:
        return None
    return MotionScript(
        channel=channel,
        mood=str(data.get("mood") or data.get("base_mood") or "").strip()[:80],
        energy=_clamp(_float(data.get("energy"), 0.35), 0.0, 1.0),
        duration=_clamp(_float(data.get("duration"), 3.0), 0.8, 8.0),
        motions=safe_motions,
        reason=str(data.get("reason") or "").strip()[:200],
    )


def _sanitize_idle_body_script(script: MotionScript | None) -> MotionScript:
    if script is None:
        return _fallback_body_script("左右待机")
    motions: list[dict[str, Any]] = []
    for motion in script.motions:
        target = str(motion.get("target") or "").lower()
        kind = str(motion.get("kind") or "").lower()
        safe = dict(motion)
        if target == "body" and kind in {"breath", "bounce"}:
            safe["kind"] = "bounce"
            safe["amplitude"] = min(0.62, max(0.22, _float(safe.get("amplitude"), 0.32)))
            safe["frequency"] = min(0.58, max(0.20, _float(safe.get("frequency"), 0.30)))
            safe.setdefault("phase", random.random())
            motions.append(safe)
            continue
        if target == "head" and kind in {"sway", "shake"}:
            axis = str(safe.get("axis") or "z").lower()
            if axis == "y":
                axis = random.choice(["x", "z"])
            safe["axis"] = axis
            if axis == "x":
                safe["amplitude"] = max(5.0, min(9.0, _float(safe.get("amplitude"), 6.2)))
                safe["frequency"] = max(0.16, min(0.36, _float(safe.get("frequency"), 0.24)))
            else:
                safe["amplitude"] = max(6.5, min(10.0, _float(safe.get("amplitude"), 7.4)))
                safe["frequency"] = max(0.22, min(0.48, _float(safe.get("frequency"), 0.32)))
            safe.setdefault("phase", random.random())
            motions.append(safe)
            continue
        if target == "head" and kind == "nod":
            # Nods read as front/back bobbing on this model; avoid them while idle.
            continue
    axes = {str(m.get("axis") or "").lower() for m in motions if str(m.get("target") or "").lower() == "head"}
    if "z" not in axes:
        motions.append({"target": "head", "kind": "sway", "axis": "z", "amplitude": random.uniform(7.0, 9.6), "frequency": random.uniform(0.24, 0.40), "phase": random.random()})
    if "x" not in axes and random.random() < 0.85:
        motions.append({"target": "head", "kind": "sway", "axis": "x", "amplitude": random.uniform(5.0, 8.2), "frequency": random.uniform(0.14, 0.30), "phase": random.random()})
    if not any(str(m.get("target")).lower() == "body" for m in motions):
        motions.append({"target": "body", "kind": "bounce", "amplitude": random.uniform(0.26, 0.48), "frequency": random.uniform(0.22, 0.36), "phase": random.random()})
    return MotionScript(
        channel="body",
        mood="左右待机",
        energy=max(0.68, min(0.95, script.energy)),
        duration=max(7.0, min(10.0, script.duration + 1.5)),
        motions=motions[:5],
        reason=script.reason or "待机时用身体轻轻左右摇摆",
    )


def _idle_face_life_script() -> MotionScript:
    return MotionScript(
        channel="face",
        mood="idle_smile",
        energy=0.45,
        duration=random.uniform(6.5, 9.5),
        motions=[
            {"target": "mouth", "kind": "smile", "value": random.uniform(0.18, 0.34)},
            {"target": "eyes", "kind": "blink", "interval": random.uniform(2.8, 4.8), "phase": random.random()},
            {"target": "eyes", "kind": "look", "x": random.uniform(-0.14, 0.14), "y": random.uniform(-0.04, 0.08)},
        ],
        reason="idle smile and natural glance",
    )


def _fallback_face_script(mood: str, energy: float = 0.35) -> MotionScript:
    return MotionScript(
        channel="face",
        mood=mood,
        energy=min(0.45, max(0.3, energy)),
        duration=random.uniform(6.5, 9.5),
        motions=[
            {"target": "mouth", "kind": "smile", "value": random.uniform(0.16, 0.30)},
            {"target": "eyes", "kind": "blink", "interval": random.uniform(2.8, 5.0), "phase": random.random()},
            {"target": "eyes", "kind": "look", "x": random.uniform(-0.12, 0.12), "y": random.uniform(-0.04, 0.07)},
        ],
        reason="本地稳定待机脸部动作",
    )


def _fallback_body_script(mood: str, energy: float = 0.35) -> MotionScript:
    energy = max(0.65, energy)
    style = random.choice(["sway", "look_left", "look_right", "bouncy"])
    if style == "look_left":
        x_phase = 0.12
    elif style == "look_right":
        x_phase = 0.62
    else:
        x_phase = random.random()
    return MotionScript(
        channel="body",
        mood=mood,
        energy=energy,
        duration=random.uniform(7.0, 10.0),
        motions=[
            {"target": "body", "kind": "bounce", "amplitude": random.uniform(0.30, 0.54), "frequency": random.uniform(0.22, 0.36), "phase": random.random()},
            {"target": "head", "kind": "sway", "axis": "z", "amplitude": random.uniform(7.0, 10.0), "frequency": random.uniform(0.24, 0.40), "phase": random.random()},
            {"target": "head", "kind": "sway", "axis": "x", "amplitude": random.uniform(5.0, 8.5), "frequency": random.uniform(0.14, 0.30), "phase": x_phase},
        ],
        reason="本地左右摇摆待机身体动作",
    )


def _direct_motion_scripts(motion: str, *, intensity: float, duration: float, reason: str) -> tuple[MotionScript, MotionScript]:
    key = (motion or "idle").lower()
    energy = max(0.25, intensity)
    amp = 3.5 + energy * 6.0
    face_motions: list[dict[str, Any]] = [
        {"target": "eyes", "kind": "blink", "interval": 2.2, "phase": random.random()},
    ]
    body_motions: list[dict[str, Any]] = [
        {"target": "body", "kind": "bounce", "amplitude": 0.45 + energy * 0.28, "frequency": 0.32},
    ]

    if key in {"smile", "happy"}:
        face_motions += [
            {"target": "mouth", "kind": "smile", "value": 0.35 + energy * 0.35},
            {"target": "eyes", "kind": "squint", "value": 0.12 + energy * 0.18},
        ]
        body_motions += [
            {"target": "head", "kind": "sway", "axis": "z", "amplitude": amp * 0.75, "frequency": 0.65},
            {"target": "body", "kind": "bounce", "amplitude": 0.55 + energy * 0.35, "frequency": 0.75},
        ]
    elif key in {"shake", "sway", "excited"}:
        face_motions += [
            {"target": "mouth", "kind": "smile", "value": 0.25 + energy * 0.35},
            {"target": "eyes", "kind": "look", "x": random.choice([-0.12, 0.12]), "y": 0.02},
        ]
        body_motions += [
            {"target": "head", "kind": "shake", "axis": "x", "amplitude": amp, "frequency": 0.85 + energy * 0.45},
            {"target": "head", "kind": "sway", "axis": "z", "amplitude": amp * 0.65, "frequency": 0.55},
            {"target": "body", "kind": "bounce", "amplitude": 0.55 + energy * 0.35, "frequency": 0.9},
        ]
    elif key == "nod":
        face_motions += [{"target": "mouth", "kind": "smile", "value": 0.18 + energy * 0.2}]
        body_motions += [
            {"target": "head", "kind": "nod", "amplitude": amp, "frequency": 0.75 + energy * 0.35},
            {"target": "body", "kind": "bounce", "amplitude": 0.45 + energy * 0.28, "frequency": 0.7},
        ]
    elif key in {"pout", "sad"}:
        face_motions += [
            {"target": "mouth", "kind": "pout", "value": 0.25 + energy * 0.25},
            {"target": "eyes", "kind": "sleepy", "value": 0.18 + energy * 0.22},
        ]
        body_motions += [
            {"target": "head", "kind": "droop", "amplitude": 1.2 + energy * 2.0},
            {"target": "head", "kind": "sway", "axis": "z", "amplitude": 0.8 + energy, "frequency": 0.24},
        ]
    elif key == "thinking":
        face_motions += [
            {"target": "eyes", "kind": "look", "x": 0.18, "y": 0.06},
            {"target": "brows", "kind": "frown", "value": 0.18 + energy * 0.15},
        ]
        body_motions += [{"target": "head", "kind": "tilt", "value": -2.0 - energy * 2.0, "amplitude": 1.0}]
    else:
        face_motions += [{"target": "mouth", "kind": "smile", "value": 0.12 + energy * 0.18}]
        body_motions += [
            {"target": "head", "kind": "sway", "axis": "x", "amplitude": amp * 0.45, "frequency": 0.35},
            {"target": "head", "kind": "sway", "axis": "z", "amplitude": amp * 0.25, "frequency": 0.28, "phase": 0.25},
        ]

    return (
        MotionScript("face", mood=key, energy=energy, duration=duration, motions=face_motions, reason=reason),
        MotionScript("body", mood=key, energy=energy, duration=duration, motions=body_motions, reason=reason),
    )


def _smooth_params(current: dict[str, float], target: dict[str, float], *, alpha: float) -> dict[str, float]:
    keys = set(current) | set(target)
    out: dict[str, float] = {}
    for key in keys:
        cv = float(current.get(key, 0.0))
        tv = float(target.get(key, 0.0))
        value = cv + (tv - cv) * alpha
        if abs(value) > 0.002:
            out[key] = value
    return out


def _envelope(elapsed: float, duration: float, *, hold_tail: bool = False) -> float:
    fade = min(2.4, max(1.2, duration * 0.34))
    if elapsed < fade:
        return _ease(elapsed / max(0.001, fade))
    if hold_tail:
        return 1.0
    if elapsed > duration - fade:
        return _ease(max(0.0, (duration - elapsed) / max(0.001, fade)))
    return 1.0


def _ease(x: float) -> float:
    x = _clamp(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def _clamp_params(params: dict[str, float]) -> dict[str, float]:
    params = {k: v for k, v in params.items() if k not in {PARAM_FACE_POS_Z, PARAM_MOCOPI_BODY_POS_Z}}
    ranges = {
        PARAM_EYE_OPEN_L: (0.0, 1.2),
        PARAM_EYE_OPEN_R: (0.0, 1.2),
        PARAM_MOUTH_OPEN: (0.0, 0.9),
        PARAM_MOUTH_SMILE: (-0.8, 1.0),
        PARAM_BROWS: (-1.0, 1.0),
        PARAM_FACE_ANGLE_X: (-10.0, 10.0),
        PARAM_FACE_ANGLE_Y: (-8.0, 8.0),
        PARAM_FACE_ANGLE_Z: (-12.0, 12.0),
        PARAM_FACE_POS_X: (-4.0, 4.0),
        PARAM_FACE_POS_Y: (-4.0, 4.0),
        PARAM_FACE_POS_Z: (-1.0, 1.0),
        PARAM_MOCOPI_BODY_ANGLE_X: (-12.0, 12.0),
        PARAM_MOCOPI_BODY_ANGLE_Y: (-10.0, 10.0),
        PARAM_MOCOPI_BODY_ANGLE_Z: (-12.0, 12.0),
        PARAM_MOCOPI_BODY_POS_X: (-1.0, 1.0),
        PARAM_MOCOPI_BODY_POS_Y: (-1.0, 1.0),
        PARAM_MOCOPI_BODY_POS_Z: (-1.0, 1.0),
        PARAM_EYE_LEFT_X: (-0.6, 0.6),
        PARAM_EYE_RIGHT_X: (-0.6, 0.6),
        PARAM_EYE_LEFT_Y: (-0.6, 0.6),
        PARAM_EYE_RIGHT_Y: (-0.6, 0.6),
    }
    return {key: _clamp(float(value), *ranges.get(key, (-1.0, 1.0))) for key, value in params.items()}


def _extract_json(text: str) -> dict | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def _safe_context(obj) -> str:
    if obj is None or not hasattr(obj, "get_context"):
        return ""
    try:
        return obj.get_context() or ""
    except Exception:
        return ""


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
