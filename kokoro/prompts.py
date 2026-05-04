"""Prompt template loading and formatting."""

from __future__ import annotations

import json
import os
from typing import Any

_PROMPTS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts.json")

_DEFAULTS: dict[str, Any] = {
    "character_system": {
        "template": (
            "你是 {name}。\n\n"
            "【设定】{description}\n"
            "【性格】{personality}{background_block}{relationship_block}\n\n"
            "请以 {name} 的身份与我对话，用自然亲切的语气。不要提及你是AI或语言模型。\n\n"
            "【要求】\n"
            "- 只说对话本身，不要有任何动作描写、表情描写或场景描写。\n"
            "- 禁止使用括号（）或方括号【】来插入动作或表情。\n"
            "- 每次回复简短自然，1-3句话。\n"
            "- 只输出你自己的对话，不要生成'玩家：'或任何其他人的台词。"
        )
    },
    "stt_refine": {
        "system": "你是一个语音识别文本整理助手。保留所有内容，只修正明显错误，不要删减。",
        "user_template": (
            "你是一个语音识别文本的整理助手。将语音识别结果整理为通顺的文本，保留所有内容，不要随意删减。\n\n"
            "要求：\n"
            "- 修正明显的识别错误，如同音字、错字。\n"
            "- 去除口吃和重复，例如“我我我想去”改成“我想去”。\n"
            "- 长句按语义拆分为短句。\n"
            "- 如果文本本来就是通顺的，直接原文输出，不要修改。\n"
            "- 不要删减内容，不要添加原文没有的内容。\n"
            "- 直接输出整理后的文本，不要多余解释。\n\n"
            "原始文本：\n{text}\n\n整理结果："
        ),
    },
    "memory_importance": {
        "user_template": (
            "判断以下对话是否包含值得长期记忆的有用信息。\n\n"
            "值得记忆的内容包括：用户的偏好、兴趣、个人背景、目标、重要事件、具体需求、习惯、观点等。\n"
            "不值得记忆的内容包括：日常问候、简短确认、无实质内容的闲聊、重复信息、纯语气词。\n\n"
            "用户：{user_msg}\n"
            "AI：{assistant_msg}\n\n"
            "请只回答“重要”或“不重要”。"
        )
    },
}

_CACHE: dict[str, Any] | None = None


def load() -> dict[str, Any]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    data = json.loads(json.dumps(_DEFAULTS, ensure_ascii=False))
    if os.path.exists(_PROMPTS_PATH):
        try:
            with open(_PROMPTS_PATH, "r", encoding="utf-8") as file:
                custom = json.load(file)
            _deep_update(data, custom)
        except Exception:
            pass
    _CACHE = data
    return data


def get(path: str, default: Any = "") -> Any:
    current: Any = load()
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def format_prompt(path: str, **values: Any) -> str:
    template = get(path, "")
    if not isinstance(template, str):
        return ""
    return template.format(**values)


def _deep_update(base: dict[str, Any], extra: dict[str, Any]) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
