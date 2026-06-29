"""Character storage and prompt construction.

Each character lives in characters/{id}/{id}.json.
Optional per-character config at characters/{id}/config.toml
overrides global config (model, url, api key, etc.).
Portrait images and metadata live in characters/{id}/portrait/.
"""

from __future__ import annotations

import json
import os
import tomllib

from kokoro.core import prompts

_CHARACTERS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "characters",
)


def load() -> dict[str, dict[str, str]]:
    """Scan characters/ directory and load all character definitions."""
    if not os.path.isdir(_CHARACTERS_DIR):
        return {}

    characters: dict[str, dict[str, str]] = {}
    for entry in sorted(os.listdir(_CHARACTERS_DIR)):
        char_dir = os.path.join(_CHARACTERS_DIR, entry)
        if not os.path.isdir(char_dir):
            continue
        char_file = os.path.join(char_dir, f"{entry}.json")
        if not os.path.isfile(char_file):
            continue
        try:
            with open(char_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("name"):
                characters[entry] = data
        except Exception:
            continue
    return characters


def build_system_prompt(char: dict[str, str], user_name: str = "你") -> str:
    name = char.get("name", "助手")
    background = char.get("background", "")
    example_dialogue = char.get("example_dialogue", "")
    scene = char.get("scene", "")
    template = char.get("system_prompt_template", "")
    if not template:
        template = prompts.get("character_system.template", "")

    scene_block = ""
    if scene:
        formatted_scene = scene.replace("{name}", name)
        scene_block = f"\n【场景参考——仅作语气底色，不是话题来源】\n{formatted_scene}\n"

    base_prompt = template.format(
        name=name,
        user_name=user_name,
        description=char.get("description", ""),
        personality=char.get("personality", ""),
        background=background,
        background_block=f"\n【背景】{background}" if background else "",
        scene_block=scene_block,
        relationship_block="",  # kept for backward compat with override templates
        example_dialogue_block=f"\n【对话示例】\n{example_dialogue}" if example_dialogue else "",
    )
    calibration = char.get("expression_calibration") or prompts.get("character_system.expression_calibration", "")
    return f"{base_prompt}\n\n{calibration}" if calibration else base_prompt


def get_display(char: dict[str, str]) -> str:
    return f"{char.get('name', '?')} - {char.get('description', '')[:40]}"


def character_dir(character_id: str) -> str:
    """Return the directory for a given character id."""
    return os.path.join(_CHARACTERS_DIR, character_id)


def portrait_dir(character_id: str) -> str:
    """Return the portrait directory for a given character id."""
    return os.path.join(_CHARACTERS_DIR, character_id, "portrait")


def load_config(character_id: str) -> dict:
    """Load per-character config from characters/{id}/config.toml.

    Keys in this file override the global ``config.toml`` when this character
    is active.  Supported overrides: ``llm_model``, ``llm_url``.

    Per-character config should only contain non-sensitive overrides.
    API keys must NOT be stored here — use ``config.json`` with a named
    config key (e.g. ``charglm_api_key``) instead.

    Returns an empty dict if no config file exists.
    """
    path = os.path.join(_CHARACTERS_DIR, character_id, "config.toml")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}
