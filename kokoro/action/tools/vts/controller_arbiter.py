"""VTS parameter layer arbiter."""

from __future__ import annotations

import asyncio


class VTSExpressionArbiter:
    """Merges parameter sets from multiple control layers.

    Priority (highest → lowest):
      1. ``tool``    — LLM explicit ``vts_expression()`` call
      2. ``lipsync``     — TTS lip-sync (only MouthOpen/MouthSmile)
      3. ``face_script`` — LLM-scripted face motions
      4. ``body_script`` — LLM-scripted body/head motions
      5. ``emotion``     — emotion-driven expression
      6. ``idle``        — idle animation (breathing, sway)

    Per-parameter: the highest-priority layer that specifies it wins.
    Runs a periodic inject loop at ``update_hz``.
    """

    LAYER_PRIORITY = [
        "idle",
        "emotion",
        "body_script",
        "face_script",
        "direct_body",
        "direct_face",
        "lipsync",
        "tool",
    ]

    def __init__(self, controller: VTSController, update_hz: float = 12.0):
        self.controller = controller
        self._period = 1.0 / update_hz
        self._layers: dict[str, dict[str, float]] = {
            "tool": {},
            "lipsync": {},
            "face_script": {},
            "body_script": {},
            "direct_face": {},
            "direct_body": {},
            "emotion": {},
            "idle": {},
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
