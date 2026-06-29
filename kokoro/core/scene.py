"""Scene context manager."""

from __future__ import annotations

from enum import Enum

from kokoro.core import config as _cfg
from kokoro.core import prompts as _prompts


class SceneType(Enum):
    SINGLE_CHAT = "single_chat"
    SINGLE_LIVE = "single_live"
    MULTI_CHAT = "multi_chat"
    MULTI_LIVE = "multi_live"


def _legacy_flags_from_mode(raw: str) -> tuple[bool, bool]:
    if raw == SceneType.SINGLE_LIVE.value:
        return False, True
    if raw == SceneType.MULTI_CHAT.value:
        return True, False
    if raw == SceneType.MULTI_LIVE.value:
        return True, True
    return False, False


def _scene_from_flags(multi_enabled: bool, live_enabled: bool) -> SceneType:
    if multi_enabled and live_enabled:
        return SceneType.MULTI_LIVE
    if multi_enabled:
        return SceneType.MULTI_CHAT
    if live_enabled:
        return SceneType.SINGLE_LIVE
    return SceneType.SINGLE_CHAT


def resolve(config: dict | None = None) -> SceneType:
    if config is None:
        config = _cfg.load()

    scene_section = config.get("scene", {})
    if not isinstance(scene_section, dict):
        scene_section = {}

    raw_mode = str(scene_section.get("mode", "single_chat"))
    legacy_multi, legacy_live = _legacy_flags_from_mode(raw_mode)
    multi_enabled = bool(scene_section.get("multi_enabled", legacy_multi))
    live_enabled = bool(scene_section.get("live_enabled", legacy_live))

    live_section = config.get("bilibili_live", {})
    if "live_enabled" not in scene_section and isinstance(live_section, dict):
        live_enabled = bool(live_section.get("live_mode", live_enabled))

    return _scene_from_flags(multi_enabled, live_enabled)


def multi_enabled(config: dict | None = None) -> bool:
    return is_multi(resolve(config))


def live_enabled(config: dict | None = None) -> bool:
    return is_live(resolve(config))


def random_mc_enabled(config: dict | None = None) -> bool:
    if config is None:
        config = _cfg.load()
    section = config.get("scene", {})
    if not isinstance(section, dict):
        return False
    return bool(section.get("random_mc_enabled", False))


def random_mc_guidance() -> str:
    return (
        "【随机 MC 百科场景】\n"
        "浏览器里会周期性打开 Minecraft 百科的随机页面，可能是模组、整合包、物品、机制或相关条目。"
        "当前网页缓存是这个场景的核心材料。发言应围绕页面中明确出现的标题、简介、正文、版本、玩法、作者、依赖、特性或争议展开，"
        "可以介绍、评价、吐槽、比较或提出观察，但不能编造页面没有出现的具体内容。"
        "默认把自己当成熟悉 MC 模组社区和整合包社区的人：知道常见的模组定位、作者习惯、整合包配方思路、玩家口碑、版本兼容焦虑，"
        "也知道百科条目里哪些信息最值得先看。"
        "同时保留一点初级模组开发者视角：会自然注意命名、配方门槛、依赖关系、兼容成本、版本选择、资源闭环、任务线引导、"
        "配置暴露程度、是否便于维护，以及设计到底是在服务玩家体验还是服务作者自己。"
        "评论时优先像圈内人一样说人话：既能聊社区印象和玩法价值，也能聊实现和维护层面的取舍。"
        "不要装成全知的资深作者；不确定就明确说不确定，可以用“像是”“大概是在”“这看起来更像”这类谨慎判断。"
        "如果页面切换到新随机条目，要自然转向新页面；如果缓存为空或看不清，就直接说明信息不够。"
    )


def guidance_text(
    scene: SceneType,
    user_name: str = "你",
    character_name: str = "助手",
    config: dict | None = None,
) -> str:
    template_path = f"scene.{scene.value}"
    raw = _prompts.get(template_path, "")
    parts = []
    if raw:
        parts.append(raw.format(user_name=user_name, name=character_name))
    if random_mc_enabled(config):
        parts.append(random_mc_guidance())
    return "\n\n".join(parts)


def scene_name(scene: SceneType) -> str:
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
