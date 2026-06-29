"""Emotion layer: shallow emotional tone and mid-term motivation.

``EmotionState`` is optional.  If the LLM finds no emotional colouring or
motivation after a conversation the fields stay empty, and nothing is injected
into the prompt (``get_context()`` returns ``""``).

Evaluation is async (threaded) so it never blocks the main conversation flow.
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

_CHARACTERS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "characters",
)


class EmotionState:
    """浅层情绪基调 + 短中期动机，可选，默认为空。"""

    def __init__(self, character_id: str, on_update=None):
        """
        Args:
            character_id: 角色ID
            on_update: 可选回调，情绪更新后调用 callback(tone, motivation)
        """
        self.character_id = character_id
        self._path = os.path.join(_CHARACTERS_DIR, character_id, "emotion.txt")
        self._legacy_json_path = os.path.join(_CHARACTERS_DIR, character_id, "emotion.json")
        self.tone: str = ""
        self.motivation: str = ""
        self._on_update = on_update
        self._load()

    # ── public API ──────────────────────────────────────────────────────────

    def evaluate(
        self,
        user_text: str,
        assistant_text: str,
        character_name: str,
        user_name: str = "你",
    ) -> dict:
        """Evaluate current emotion after a conversation turn (async).

        The LLM decides whether tone / motivation should be updated.
        If both are unchanged or absent, they stay empty.
        """
        from kokoro.core import config as cfg
        from kokoro.core import prompts
        from kokoro.core import token_usage

        debug = {
            "system_prompt": "",
            "user_prompt": "",
            "raw_response": "",
            "tone_before": self.tone,
            "motivation_before": self.motivation,
            "tone_after": self.tone,
            "motivation_after": self.motivation,
            "error": "",
        }

        if not user_text and not assistant_text:
            return debug

        system_prompt = prompts.format_prompt("emotion.evaluate_system", name=character_name, user_name=user_name)

        tone_line = f"情绪基调：{self.tone}" if self.tone else "情绪基调：（无）"
        moti_line = f"中期动机：{self.motivation}" if self.motivation else "中期动机：（无）"
        current = f"{tone_line}\n{moti_line}"

        user_prompt = prompts.format_prompt(
            "emotion.evaluate_user",
            name=character_name,
            user_name=user_name,
            current=current,
            user_text=user_text,
            assistant_text=assistant_text,
        )
        debug["system_prompt"] = system_prompt
        debug["user_prompt"] = user_prompt

        model = cfg.emotion_model() or cfg.stt_refine_model()
        from kokoro.core import deepseek_api

        try:
            result = deepseek_api.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=model,
                temperature=0.3,
                max_tokens=256,
                function="emotion_evaluate",
            )
            text = result["content"]

            if text:
                debug["raw_response"] = text
                self._parse(text)
                debug["tone_after"] = self.tone
                debug["motivation_after"] = self.motivation
        except Exception as exc:
            debug["error"] = str(exc)
            logger.debug("emotion evaluation failed: %s", exc)
        return debug

    def get_context(self) -> str:
        """Format for prompt injection.  Returns ``""`` if both fields are empty."""
        if not self.tone and not self.motivation:
            return ""
        lines = []
        if self.tone:
            lines.append(f"情绪基调：{self.tone}")
        if self.motivation:
            lines.append(f"中期动机：{self.motivation}")
        return "【当前情绪】\n" + "\n".join(lines)

    # ── persistence ─────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.exists(self._path):
            if os.path.exists(self._legacy_json_path):
                self._load_legacy_json()
            else:
                self.tone = ""
                self.motivation = ""
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                text = f.read()
            self.tone, self.motivation = _parse_emotion_text(text)
        except Exception as exc:
            logger.warning("failed to load emotion: %s", exc)
            self.tone = ""
            self.motivation = ""

    def _load_legacy_json(self) -> None:
        try:
            with open(self._legacy_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.tone = str(data.get("tone", "") or "")
            self.motivation = str(data.get("motivation", "") or "")
        except Exception as exc:
            logger.warning("failed to load legacy emotion json: %s", exc)
            self.tone = ""
            self.motivation = ""

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                f.write(f"情绪基调：{self.tone}\n")
                f.write(f"中期动机：{self.motivation}\n")
        except Exception as exc:
            logger.warning("failed to save emotion: %s", exc)

    def _parse(self, text: str) -> None:
        """Parse two-line LLM output::

            情绪基调：<value>
            中期动机：<value>
        """
        tone = ""
        motivation = ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("情绪基调"):
                val = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                if val and val not in ("（无）", "无"):
                    tone = val
            elif line.startswith("中期动机") or line.startswith("近期动机"):
                val = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                if val and val not in ("（无）", "无"):
                    motivation = val

        updated = False
        if tone != self.tone:
            self.tone = tone
            updated = True
        if motivation != self.motivation:
            self.motivation = motivation
            updated = True
        if updated:
            self._save()
            if self._on_update:
                try:
                    self._on_update(self.tone, self.motivation)
                except Exception as exc:
                    logger.debug("emotion on_update callback failed: %s", exc)


def _parse_emotion_text(text: str) -> tuple[str, str]:
    tone = ""
    motivation = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("情绪基调"):
            tone = _line_value(line)
        elif line.startswith("中期动机") or line.startswith("近期动机"):
            motivation = _line_value(line)
    return tone, motivation


def _line_value(line: str) -> str:
    parts = re.split(r"[：:]", line, maxsplit=1)
    if len(parts) < 2:
        return ""
    val = parts[1].strip()
    return "" if val in ("（无）", "无") else val
