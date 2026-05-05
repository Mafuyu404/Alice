"""Character storage and prompt construction."""

from __future__ import annotations

import json
import os

from kokoro import prompts

_CHARACTERS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "characters.json",
)

DEFAULT_CHARACTERS: dict[str, dict[str, str]] = {
    "yuki": {
        "name": "Yuki",
        "description": "一个温柔体贴的少女",
        "personality": "温柔、体贴、偶尔调皮",
        "background": "和玩家住在同一栋公寓的邻居",
        "greeting": "你好呀！今天过得怎么样？",
        "example_dialogue": "玩家：我回来了。\nYuki：欢迎回来！今天工作辛苦了，要喝杯茶吗？",
    }
}


def load() -> dict[str, dict[str, str]]:
    if os.path.exists(_CHARACTERS_PATH):
        with open(_CHARACTERS_PATH, "r", encoding="utf-8") as file:
            return json.load(file)
    return dict(DEFAULT_CHARACTERS)


def save(characters: dict) -> None:
    with open(_CHARACTERS_PATH, "w", encoding="utf-8") as file:
        json.dump(characters, file, indent=2, ensure_ascii=False)


def build_system_prompt(char: dict[str, str]) -> str:
    name = char.get("name", "助手")
    background = char.get("background", "")
    relationship = char.get("relationship", "")
    base_prompt = prompts.format_prompt(
        "character_system.template",
        name=name,
        description=char.get("description", ""),
        personality=char.get("personality", ""),
        background=background,
        relationship=relationship,
        background_block=f"\n【背景】{background}" if background else "",
        relationship_block=f"\n【关系】{relationship}" if relationship else "",
        example_dialogue=char.get("example_dialogue", ""),
    )
    calibration = prompts.get("character_system.expression_calibration", "")
    return f"{base_prompt}\n\n{calibration}" if calibration else base_prompt


def get_display(char: dict[str, str]) -> str:
    return f"{char.get('name', '?')} - {char.get('description', '')[:40]}"
