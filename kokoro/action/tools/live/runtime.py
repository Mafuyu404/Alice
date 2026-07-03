"""Live-input capability wiring."""

from __future__ import annotations

from dataclasses import dataclass

from kokoro.action.tools.live import bilibili
from kokoro.core import config as cfg
from kokoro.core import prompts
from kokoro.core import scene as scene_mod


@dataclass
class LiveRuntime:
    bilibili_manager: bilibili.BilibiliLiveManager | None = None
    bilibili_enabled: bool = False
    live_mode: bool = False
    room_id: int = 0

    @property
    def bilibili_active(self) -> bool:
        return self.bilibili_enabled and self.bilibili_manager is not None

    def augment_context(self, command_context: str) -> str:
        if not (self.bilibili_enabled and self.live_mode):
            return command_context
        live_context = prompts.get("cli.bilibili_live_context", "")
        if command_context:
            return f"{live_context}\n\n{command_context}"
        return live_context

    def stop(self) -> None:
        if self.bilibili_manager is not None:
            self.bilibili_manager.stop()


def start(*, config: dict, room_override: int | None = None) -> LiveRuntime:
    room_id = room_override if room_override is not None else cfg.bilibili_live_room_id()
    live_mode = scene_mod.live_enabled(config)
    enabled = cfg.bilibili_live_enabled() and room_id > 0
    runtime = LiveRuntime(
        bilibili_enabled=enabled,
        live_mode=live_mode,
        room_id=room_id,
    )
    if enabled:
        runtime.bilibili_manager = bilibili.BilibiliLiveManager(
            room_id=room_id,
            buffer_max_age=cfg.bilibili_live_buffer_max_age(),
        )
        runtime.bilibili_manager.start()
    elif room_override is not None and room_id > 0:
        print(f"  [bilibili] Room {room_id} set but bilibili_live.enabled = false in config")
    return runtime
