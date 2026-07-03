"""VTS idle animation loop."""

from __future__ import annotations

import asyncio
import math
import random
import time

from kokoro.action.tools.vts.controller_params import (
    PARAM_EYE_OPEN_L,
    PARAM_EYE_OPEN_R,
    PARAM_FACE_ANGLE_X,
    PARAM_FACE_POS_Y,
)


class VTSIdleLoop:
    """Background task driving idle animations via the arbiter.

    - **Blink**: random interval 3-6s, closes eyes for 150ms
    - **Breathing**: subtle ``FacePositionY`` sine wave at ~0.25Hz
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
                idle_params[PARAM_FACE_POS_Y] = math.sin(elapsed * math.pi / 2.0) * self.breathing_amp * 0.5
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
