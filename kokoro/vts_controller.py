"""VTube Studio integration — core connection and parameter injection.

Sub-modules:
  vts_controller     VTSController (connection, auth, inject, expressions)
  vts_lipsync        VTSLipSync (audio energy → MouthOpen)
  vts_arbiter        VTSExpressionArbiter (merge layers, periodic inject)
  vts_idle           VTSIdleLoop (background idle animation)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import time
from typing import Any

import numpy as np
import pyvts

logger = logging.getLogger(__name__)

PLUGIN_NAME = "alice-vts"
DEVELOPER = "Alice"
TOKEN_PATH = "./vts_token.txt"

# Tracking parameter name constants
PARAM_EYE_OPEN_L = "EyeOpenLeft"
PARAM_EYE_OPEN_R = "EyeOpenRight"
PARAM_MOUTH_OPEN = "MouthOpen"
PARAM_MOUTH_SMILE = "MouthSmile"
PARAM_BROWS = "Brows"
PARAM_FACE_ANGLE_X = "FaceAngleX"
PARAM_FACE_ANGLE_Y = "FaceAngleY"
PARAM_FACE_ANGLE_Z = "FaceAngleZ"
PARAM_FACE_POS_Z = "FacePositionZ"
PARAM_EYE_LEFT_X = "EyeLeftX"
PARAM_EYE_LEFT_Y = "EyeLeftY"
PARAM_EYE_RIGHT_X = "EyeRightX"
PARAM_EYE_RIGHT_Y = "EyeRightY"

_ALL_VALID_PARAMS = {
    PARAM_EYE_OPEN_L, PARAM_EYE_OPEN_R, PARAM_MOUTH_OPEN,
    PARAM_MOUTH_SMILE, PARAM_BROWS, PARAM_FACE_ANGLE_X,
    PARAM_FACE_ANGLE_Y, PARAM_FACE_ANGLE_Z, PARAM_FACE_POS_Z,
    PARAM_EYE_LEFT_X, PARAM_EYE_LEFT_Y, PARAM_EYE_RIGHT_X,
    PARAM_EYE_RIGHT_Y,
}

# ── Default expression presets ──────────────────────────────────────────────

DEFAULT_EXPRESSIONS: dict[str, dict[str, float]] = {
    "neutral":   {PARAM_MOUTH_SMILE: 0, PARAM_BROWS: 0.5,
                  PARAM_EYE_OPEN_L: 1, PARAM_EYE_OPEN_R: 1},
    "happy":     {PARAM_MOUTH_SMILE: 0.7, PARAM_BROWS: 0.3},
    "smile":     {PARAM_MOUTH_SMILE: 0.5, PARAM_BROWS: 0.4},
    "sad":       {PARAM_MOUTH_SMILE: 0, PARAM_BROWS: 0.8, PARAM_MOUTH_OPEN: 0.1},
    "angry":     {PARAM_BROWS: 1, PARAM_MOUTH_SMILE: 0, PARAM_MOUTH_OPEN: 0.15},
    "surprised": {PARAM_EYE_OPEN_L: 1, PARAM_EYE_OPEN_R: 1,
                  PARAM_MOUTH_OPEN: 0.6, PARAM_BROWS: 0.8},
    "tired":     {PARAM_EYE_OPEN_L: 0.5, PARAM_EYE_OPEN_R: 0.5,
                  PARAM_MOUTH_SMILE: 0, PARAM_BROWS: 0.3},
    "thinking":  {PARAM_EYE_LEFT_X: 0.15, PARAM_BROWS: 0.5},
    "shy":       {PARAM_MOUTH_SMILE: 0.3, PARAM_BROWS: 0.6,
                  PARAM_EYE_OPEN_L: 0.8, PARAM_EYE_OPEN_R: 0.8},
    "excited":   {PARAM_MOUTH_SMILE: 0.9, PARAM_EYE_OPEN_L: 1, PARAM_EYE_OPEN_R: 1,
                  PARAM_BROWS: 0.2},
    "wink":      {PARAM_EYE_OPEN_L: 0, PARAM_EYE_OPEN_R: 1, PARAM_MOUTH_SMILE: 0.4},
    "pout":      {PARAM_MOUTH_SMILE: 0, PARAM_MOUTH_OPEN: 0.2, PARAM_BROWS: 0.7},
    "sigh":      {PARAM_MOUTH_OPEN: 0.3, PARAM_MOUTH_SMILE: 0, PARAM_BROWS: 0.6},
    "doubt":     {PARAM_EYE_LEFT_X: 0.1, PARAM_BROWS: 0.6, PARAM_MOUTH_SMILE: 0},
    "cry":       {PARAM_MOUTH_SMILE: 0, PARAM_BROWS: 0.9, PARAM_MOUTH_OPEN: 0.2,
                  PARAM_EYE_OPEN_L: 0.7, PARAM_EYE_OPEN_R: 0.7},
    "awkward":   {PARAM_MOUTH_SMILE: 0.2, PARAM_BROWS: 0.5, PARAM_EYE_LEFT_X: 0.08},
}

DEFAULT_EMOTION_KEYWORDS: dict[str, str] = {
    "开心": "happy", "高兴": "happy", "快乐": "happy", "愉悦": "happy",
    "难过": "sad", "伤心": "sad", "悲伤": "sad", "沮丧": "sad",
    "生气": "angry", "愤怒": "angry", "恼火": "angry", "烦躁": "angry",
    "惊讶": "surprised", "震惊": "surprised", "意外": "surprised",
    "疲惫": "tired", "累": "tired", "疲倦": "tired", "困": "tired",
    "思考": "thinking", "思索": "thinking", "琢磨": "thinking",
    "害羞": "shy", "不好意思": "shy",
    "兴奋": "excited", "激动": "excited",
    "疑惑": "doubt", "怀疑": "doubt", "困惑": "doubt",
    "无奈": "sigh", "叹息": "sigh",
    "尴尬": "awkward", "窘": "awkward",
}

DEFAULT_LIPSYNC_CONFIG = {
    "enabled": True,
    "energy_multiplier": 3.0,
    "smooth_factor": 0.7,
    "mouth_open_min": 0.05,
    "mouth_open_max": 0.85,
    "mouth_smile_amount": 0.15,
}


def _load_vts_mapping(character_id: str) -> dict | None:
    path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "characters", character_id, "vts_mapping.json",
    )
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("failed to load vts_mapping for %s: %s", character_id, exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# VTSController — core
# ═══════════════════════════════════════════════════════════════════════════

class VTSController:
    """Manages connection, authentication, and parameter injection for VTS."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8001,
        plugin_name: str = PLUGIN_NAME,
        developer: str = DEVELOPER,
        token_path: str = TOKEN_PATH,
        character_id: str = "",
    ):
        self.character_id = character_id
        self.host = host
        self.port = port

        plugin_info = {
            "plugin_name": plugin_name,
            "developer": developer,
            "authentication_token_path": token_path,
        }
        vts_api_info = {
            "host": host, "port": port,
            "version": "1.0", "name": "VTubeStudioPublicAPI",
        }
        self.vts = pyvts.vts(plugin_info=plugin_info, vts_api_info=vts_api_info)
        self.myvts = pyvts.vts_request.VTSRequest(
            developer=developer, plugin_name=plugin_name,
        )
        self._connected = False
        self._authenticated = False

        # Load expression data
        self._init_expressions(character_id)

    def _init_expressions(self, character_id: str) -> None:
        mapping = _load_vts_mapping(character_id) if character_id else None
        if mapping:
            raw_expr = mapping.get("expressions", {})
            self.expressions: dict[str, dict[str, float]] = {}
            for expr_id, params in raw_expr.items():
                self.expressions[expr_id] = {
                    k: float(v) for k, v in params.items() if k in _ALL_VALID_PARAMS
                }
            # Fill missing defaults
            for name, default_params in DEFAULT_EXPRESSIONS.items():
                self.expressions.setdefault(name, dict(default_params))
            self.emotion_keywords = mapping.get("emotion_keywords", DEFAULT_EMOTION_KEYWORDS)
            self.lipsync_config = {**DEFAULT_LIPSYNC_CONFIG, **(mapping.get("lipsync", {}))}
        else:
            self.expressions = {k: dict(v) for k, v in DEFAULT_EXPRESSIONS.items()}
            self.emotion_keywords = dict(DEFAULT_EMOTION_KEYWORDS)
            self.lipsync_config = dict(DEFAULT_LIPSYNC_CONFIG)

    # ── Connection ──────────────────────────────────────────────────────────

    async def connect(self) -> None:
        if self._connected:
            return
        await self.vts.connect()
        self._connected = True
        logger.info("VTS connected %s:%s", self.host, self.port)

    async def authenticate(self) -> None:
        if self._authenticated:
            return
        await self.connect()
        await self.vts.request_authenticate_token()
        ok = await self.vts.request_authenticate()
        if not ok:
            raise RuntimeError("VTS auth failed — accept plugin in VTS")
        self._authenticated = True
        logger.info("VTS authenticated")

    # ── Injection ───────────────────────────────────────────────────────────

    async def inject(
        self,
        params: dict[str, float],
        face_found: bool = True,
        mode: str = "set",
        weight: float = 1.0,
    ) -> dict[str, Any]:
        if not params:
            return {}
        await self.authenticate()
        req = self.myvts.requestSetMultiParameterValue(
            parameters=list(params.keys()),
            values=list(params.values()),
            face_found=face_found,
            mode=mode,
            weight=weight,
        )
        try:
            return await self.vts.request(req)
        except Exception as exc:
            logger.debug("VTS inject failed: %s", exc)
            return {}

    async def set_parameter(self, name: str, value: float) -> dict[str, Any]:
        return await self.inject({name: value})

    async def get_parameter(self, name: str) -> float | None:
        await self.authenticate()
        req = self.myvts.requestParameterValue(parameter=name)
        resp = await self.vts.request(req)
        data = resp.get("data", {})
        if "parameterValues" in data:
            return data["parameterValues"].get(name)
        return None

    async def get_tracking_parameters(self) -> list[dict[str, Any]]:
        await self.authenticate()
        req = self.myvts.requestTrackingParameterList()
        resp = await self.vts.request(req)
        return (
            resp.get("data", {}).get("defaultParameters", [])
            + resp.get("data", {}).get("customParameters", [])
        )

    # ── Expression helpers ──────────────────────────────────────────────────

    def has_expression(self, expr_id: str) -> bool:
        return expr_id in self.expressions

    def get_expression_params(self, expr_id: str, intensity: float = 1.0) -> dict[str, float]:
        base = self.expressions.get(expr_id, self.expressions.get("neutral", {}))
        if intensity >= 1.0:
            return dict(base)
        neutral = self.expressions.get("neutral", {})
        return {
            k: neutral.get(k, 0.0) + (v - neutral.get(k, 0.0)) * intensity
            for k, v in base.items()
        }

    def resolve_emotion_tone(self, tone: str) -> str | None:
        if not tone:
            return None
        if tone in self.expressions:
            return tone
        for keyword, expr_id in self.emotion_keywords.items():
            if keyword in tone:
                return expr_id
        return None

    async def set_expression(self, expr_id: str, intensity: float = 1.0) -> None:
        params = self.get_expression_params(expr_id, intensity)
        if params:
            await self.inject(params)

    async def blink(self, speed: float = 0.12) -> None:
        await self.inject({PARAM_EYE_OPEN_L: 0.0, PARAM_EYE_OPEN_R: 0.0})
        await asyncio.sleep(speed)
        await self.inject({PARAM_EYE_OPEN_L: 1.0, PARAM_EYE_OPEN_R: 1.0})

    async def close(self) -> None:
        if self._connected:
            await self.vts.close()
            self._connected = False
            self._authenticated = False
            logger.info("VTS disconnected")


# ═══════════════════════════════════════════════════════════════════════════
# VTSLipSync — audio energy → MouthOpen
# ═══════════════════════════════════════════════════════════════════════════

class VTSLipSync:
    """Listens to TTS audio frames and maps RMS energy to MouthOpen.

    Hooks into ``StreamingTTS.on_audio_frame``. Uses EMA smoothing so mouth
    movement follows natural speech cadence rather than frame-level noise.

    The ``lipsync`` layer feeds into the arbiter if one is provided, otherwise
    injects directly (less coordinated with other layers).
    """

    def __init__(self, controller: VTSController, arbiter: VTSExpressionArbiter | None = None, loop: asyncio.AbstractEventLoop | None = None):
        self.controller = controller
        self.arbiter = arbiter
        self._loop = loop
        cfg = controller.lipsync_config
        self.energy_mult = float(cfg["energy_multiplier"])
        self.smooth_factor = float(cfg["smooth_factor"])
        self.mouth_min = float(cfg["mouth_open_min"])
        self.mouth_max = float(cfg["mouth_open_max"])
        self.smile_amount = float(cfg["mouth_smile_amount"])
        self._smoothed = 0.0
        self._active = False
        self._last_inject = 0.0
        self._inject_interval = 1.0 / 25  # 25Hz

    def start(self) -> None:
        self._active = True
        self._smoothed = 0.0

    def stop(self) -> None:
        self._active = False
        self._smoothed = 0.0

    def on_audio_frame(self, chunk: np.ndarray) -> None:
        if not self._active:
            self.start()  # auto-start on first audio frame
            if not self._active:  # still not active after start()
                return

        rms = float(np.sqrt(np.mean(np.square(chunk.astype(np.float64)))))
        raw = min(rms * self.energy_mult, self.mouth_max)
        if raw < self.mouth_min:
            raw = 0.0

        sf = self.smooth_factor
        self._smoothed = self._smoothed * sf + raw * (1 - sf)

        now = time.monotonic()
        if now - self._last_inject < self._inject_interval:
            return
        self._last_inject = now

        params = {PARAM_MOUTH_OPEN: self._smoothed}
        if self._smoothed > 0.1:
            params[PARAM_MOUTH_SMILE] = self.smile_amount

        if self.arbiter:
            self.arbiter.set_layer("lipsync", params)
        elif self._loop is not None:
            asyncio.run_coroutine_threadsafe(
                self.controller.inject(params),
                self._loop,
            )


# ═══════════════════════════════════════════════════════════════════════════
# VTSExpressionArbiter — merge inputs from all layers
# ═══════════════════════════════════════════════════════════════════════════

class VTSExpressionArbiter:
    """Merges parameter sets from multiple control layers.

    Priority (highest → lowest):
      1. ``tool``    — LLM explicit ``vts_expression()`` call
      2. ``lipsync`` — TTS lip-sync (only MouthOpen/MouthSmile)
      3. ``emotion`` — emotion-driven expression
      4. ``idle``    — idle animation (breathing, sway)

    Per-parameter: the highest-priority layer that specifies it wins.
    Runs a periodic inject loop at ``update_hz``.
    """

    LAYER_PRIORITY = ["idle", "emotion", "lipsync", "tool"]

    def __init__(self, controller: VTSController, update_hz: float = 12.0):
        self.controller = controller
        self._period = 1.0 / update_hz
        self._layers: dict[str, dict[str, float]] = {
            "tool": {}, "lipsync": {}, "emotion": {}, "idle": {},
        }
        self._running = False
        self._task: asyncio.Task | None = None

    def set_layer(self, layer: str, params: dict[str, float]) -> None:
        if layer in self._layers:
            self._layers[layer] = dict(params)

    def clear_layer(self, layer: str) -> None:
        if layer in self._layers:
            self._layers[layer] = {}

    def merge(self) -> dict[str, float]:
        merged: dict[str, float] = {}
        for layer in self.LAYER_PRIORITY:
            merged.update(self._layers[layer])
        return merged

    async def _loop(self) -> None:
        while self._running:
            params = self.merge()
            if params:
                await self.controller.inject(params)
            await asyncio.sleep(self._period)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
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


# ═══════════════════════════════════════════════════════════════════════════
# VTSIdleLoop — background idle animation
# ═══════════════════════════════════════════════════════════════════════════

class VTSIdleLoop:
    """Background task driving idle animations via the arbiter.

    - **Blink**: random interval 3-6s, closes eyes for 150ms
    - **Breathing**: ``FacePositionZ`` sine wave at ~0.25Hz
    - **Head sway**: ``FaceAngleX`` slow drift

    Skips blink when ``tts_active`` is True.
    """

    def __init__(
        self,
        arbiter: VTSExpressionArbiter,
        blink_min: float = 3.0,
        blink_max: float = 6.0,
        breathing_amp: float = 0.3,
        head_sway_amp: float = 1.0,
    ):
        self.arbiter = arbiter
        self.blink_min = blink_min
        self.blink_max = blink_max
        self.breathing_amp = breathing_amp
        self.head_sway_amp = head_sway_amp
        self.tts_active = False
        self._running = False
        self._task: asyncio.Task | None = None
        self._t0 = 0.0
        self._blink_until = 0.0  # blink eye-close duration window

    def set_tts_active(self, active: bool) -> None:
        self.tts_active = active

    async def _loop(self) -> None:
        self._t0 = time.monotonic()
        next_blink = self._t0 + random.uniform(self.blink_min, self.blink_max)
        BLINK_DURATION = 0.08  # 80ms

        while self._running:
            now = time.monotonic()
            elapsed = now - self._t0

            # Continuous: breathing + sway + eyes always open by default
            idle_params: dict[str, float] = {
                PARAM_EYE_OPEN_L: 1.0,
                PARAM_EYE_OPEN_R: 1.0,
            }
            if self.breathing_amp > 0:
                idle_params[PARAM_FACE_POS_Z] = math.sin(elapsed * math.pi / 2.0) * self.breathing_amp
            if self.head_sway_amp > 0:
                idle_params[PARAM_FACE_ANGLE_X] = math.sin(elapsed * 0.7) * self.head_sway_amp * 0.5
            # Periodic blink: set close window, auto-opens after BLINK_DURATION
            if now >= next_blink and not self.tts_active:
                self._blink_until = now + BLINK_DURATION
                next_blink = time.monotonic() + random.uniform(self.blink_min, self.blink_max)

            if now < self._blink_until:
                idle_params[PARAM_EYE_OPEN_L] = 0.0
                idle_params[PARAM_EYE_OPEN_R] = 0.0

            self.arbiter.set_layer("idle", idle_params)
            await asyncio.sleep(0.05)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
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
