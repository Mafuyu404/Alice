"""Scene context manager — guides LLM behavior by conversation scene.

Scenes define the information source layout and role context for the LLM.
Each scene has its own guidance template that sits alongside the character
system prompt to tell the model what kind of input to expect.
"""

from __future__ import annotations

from enum import Enum

from kokoro import config as _cfg
from kokoro import prompts as _prompts


class SceneType(Enum):
    SINGLE_CHAT = "single_chat"   # 单人普通对话 — 仅用户和 AI
    SINGLE_LIVE = "single_live"   # 单人直播 — 用户 + 弹幕
    MULTI_CHAT = "multi_chat"     # 多人普通对话 — 多人轮流发言
    MULTI_LIVE = "multi_live"     # 多人直播 — 多人 + 弹幕


# ── Resolve ────────────────────────────────────────────────────────────────


def resolve(config: dict | None = None) -> SceneType:
    """Return the active scene based on runtime config.

    Priority:
      1. If ``scene.mode`` is set explicitly in config, use it.
      2. If ``bilibili_live.live_mode`` is true, override to a LIVE variant.
    """
    if config is None:
        config = _cfg.load()

    # Read explicit scene mode from config
    raw = "single_chat"
    scene_section = config.get("scene", {})
    if isinstance(scene_section, dict):
        raw = str(scene_section.get("mode", "single_chat"))

    # Normalise to enum
    if raw not in SceneType._value2member_map_:
        raw = "single_chat"
    scene = SceneType(raw)

    # Live override: if bilibili_live is live, promote to LIVE variant
    live_section = config.get("bilibili_live", {})
    if isinstance(live_section, dict) and live_section.get("live_mode", False):
        if scene == SceneType.SINGLE_CHAT:
            scene = SceneType.SINGLE_LIVE
        elif scene == SceneType.MULTI_CHAT:
            scene = SceneType.MULTI_LIVE

    return scene


# ── Guidance builder ────────────────────────────────────────────────────────


def guidance_text(scene: SceneType, user_name: str = "你", character_name: str = "助手") -> str:
    """Return the scene guidance block for the given scene.

    This is injected as a system-level context message to tell the LLM
    what kind of conversation layout to expect.
    """
    template_path = f"scene.{scene.value}"
    raw = _prompts.get(template_path, "")
    if not raw:
        return ""
    return raw.format(user_name=user_name, name=character_name)


def scene_name(scene: SceneType) -> str:
    """Human-readable Chinese scene name."""
    names = {
        SceneType.SINGLE_CHAT: "日常对话",
        SceneType.SINGLE_LIVE: "单人直播",
        SceneType.MULTI_CHAT: "多人对话",
        SceneType.MULTI_LIVE: "多人直播",
    }
    return names.get(scene, "日常对话")


def is_live(scene: SceneType) -> bool:
    return scene in (SceneType.SINGLE_LIVE, SceneType.MULTI_LIVE)


def is_multi(scene: SceneType) -> bool:
    return scene in (SceneType.MULTI_CHAT, SceneType.MULTI_LIVE)
