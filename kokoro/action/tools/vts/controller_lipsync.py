"""VTS lip-sync layer driven by TTS audio energy."""

from __future__ import annotations

import asyncio
import math
import time

import numpy as np

from kokoro.action.tools.vts.controller_params import PARAM_MOUTH_OPEN


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
        self.mouth_max = min(float(cfg["mouth_open_max"]), 0.62)
        self.smile_amount = float(cfg["mouth_smile_amount"])
        self._smoothed = 0.0
        self._previous_raw = 0.0
        self._noise_floor = 0.01
        self._active = False
        self._last_inject = 0.0
        self._inject_interval = 1.0 / 25  # 25Hz

    def start(self) -> None:
        self._active = True
        self._smoothed = 0.0
        self._previous_raw = 0.0
        self._noise_floor = 0.01

    def stop(self) -> None:
        self._active = False
        self._smoothed = 0.0
        self._previous_raw = 0.0
        if self.arbiter:
            self.arbiter.clear_layer("lipsync")

    def on_audio_frame(self, chunk: np.ndarray) -> None:
        if not self._active:
            self.start()  # auto-start on first audio frame
            if not self._active:  # still not active after start()
                return

        audio = chunk.astype(np.float64)
        rms = float(np.sqrt(np.mean(np.square(audio))))
        self._noise_floor = self._noise_floor * 0.96 + min(rms, 0.08) * 0.04
        normalized = max(0.0, rms - self._noise_floor * 0.8)
        raw = min(math.sqrt(normalized) * self.energy_mult * 0.28, self.mouth_max)
        if raw < self.mouth_min * 0.75:
            raw = 0.0

        delta = max(0.0, raw - self._previous_raw)
        self._previous_raw = raw
        shaped = min(raw + delta * 0.22, self.mouth_max)

        attack = 0.68
        release = 0.42
        if shaped > self._smoothed:
            self._smoothed += (shaped - self._smoothed) * attack
        else:
            self._smoothed = self._smoothed * release + shaped * (1 - release)

        now = time.monotonic()
        if now - self._last_inject < self._inject_interval:
            return
        self._last_inject = now

        params = {PARAM_MOUTH_OPEN: self._smoothed}

        if self.arbiter:
            self.arbiter.set_layer("lipsync", params)
        elif self._loop is not None:
            asyncio.run_coroutine_threadsafe(
                self.controller.inject(params),
                self._loop,
            )
