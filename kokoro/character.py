"""Character storage and prompt construction."""

from __future__ import annotations

import json
import os

from kokoro import prompts

_CHARACTERS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "characters.json",
)


def load() -> dict[str, dict[str, str]]:
    if os.path.exists(_CHARACTERS_PATH):
        with open(_CHARACTERS_PATH, "r", encoding="utf-8") as file:
            return json.load(file)
    return {}


def save(characters: dict) -> None:
    with open(_CHARACTERS_PATH, "w", encoding="utf-8") as file:
        json.dump(characters, file, indent=2, ensure_ascii=False)


def build_system_prompt(char: dict[str, str]) -> str:
    name = char.get("name", "助手")
    background = char.get("background", "")
    relationship = char.get("relationship", "")
    example_dialogue = char.get("example_dialogue", "")
    base_prompt = prompts.format_prompt(
        "character_system.template",
        name=name,
        description=char.get("description", ""),
        personality=char.get("personality", ""),
        background=background,
        relationship=relationship,
        background_block=f"\n【背景】{background}" if background else "",
        relationship_block=f"\n【关系】{relationship}" if relationship else "",
        example_dialogue_block=f"\n【对话示例】\n{example_dialogue}" if example_dialogue else "",
    )
    calibration = prompts.get("character_system.expression_calibration", "")
    return f"{base_prompt}\n\n{calibration}" if calibration else base_prompt


def get_display(char: dict[str, str]) -> str:
    return f"{char.get('name', '?')} - {char.get('description', '')[:40]}"
