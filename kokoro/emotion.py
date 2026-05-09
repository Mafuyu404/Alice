"""Emotion layer — shallow emotional tone and mid-term motivation.

``EmotionState`` is optional.  If the LLM finds no emotional colouring or
motivation after a conversation the fields stay empty, and nothing is injected
into the prompt (``get_context()`` returns ``""``).

Evaluation is async (threaded) so it never blocks the main conversation flow.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_CHARACTERS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "characters",
)


class EmotionState:
    """浅层情绪基调 + 短中期动机，可选，默认为空。"""

    def __init__(self, character_id: str):
        self.character_id = character_id
        self._path = os.path.join(_CHARACTERS_DIR, character_id, "emotion.json")
        self.tone: str = ""
        self.motivation: str = ""
        self._load()

    # ── public API ──────────────────────────────────────────────────────────

    def evaluate(
        self,
        user_text: str,
        assistant_text: str,
        character_name: str,
    ) -> None:
        """Evaluate current emotion after a conversation turn (async).

        The LLM decides whether tone / motivation should be updated.
        If both are unchanged or absent, they stay empty.
        """
        from kokoro import config as cfg
        from kokoro import token_usage

        if not user_text and not assistant_text:
            return

        system_prompt = (
            f"你是{character_name}的情绪系统。根据最近的对话，评估"
            f"{character_name}当前的情绪状态和短期动机。"
        )

        tone_line = f"情绪基调：{self.tone}" if self.tone else "情绪基调：（无）"
        moti_line = f"近期动机：{self.motivation}" if self.motivation else "近期动机：（无）"
        current = f"{tone_line}\n{moti_line}"

        user_prompt = (
            f"当前情绪：\n{current}\n\n"
            f"最近的对话：\n"
            f"用户：{user_text}\n"
            f"{character_name}：{assistant_text}\n\n"
            "请输出更新后的情绪状态。\n\n"
            "情绪基调是浅层的：比如因为对方敷衍而不开心，被夸奖了所以开心。\n"
            "近期动机是接下来想做的事：比如发现对方沮丧所以想让他振作起来。\n\n"
            "如果没有明显情绪或动机，对应项留空即可。\n\n"
            "输出格式（只输出两行，不要多余文字）：\n"
            "情绪基调：\n"
            "近期动机："
        )

        model = cfg.emotion_model() or cfg.stt_refine_model()
        url = cfg.llm_url()
        api_key = ""
        openai_compatible = False
        if cfg.is_deepseek_model(model):
            api_key = cfg.deepseek_api_key()
            url = cfg.deepseek_url()
            openai_compatible = True

        headers = {"Content-Type": "application/json"}
        if openai_compatible:
            headers["Authorization"] = f"Bearer {api_key}"
            import re as _re
            base_url = url.rstrip("/")
            if not _re.search(r"/v\d+$", base_url):
                base_url += "/v1"
            api_url = f"{base_url}/chat/completions"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 256,
            }
        else:
            api_url = f"{url}/api/chat"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 256},
            }

        try:
            import urllib.request

            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())

            if openai_compatible:
                usage = result.get("usage", {})
                pt = int(usage.get("prompt_tokens", 0))
                ct = int(usage.get("completion_tokens", 0))
                if pt or ct:
                    token_usage.record(model, "emotion_evaluate", pt, ct)
                text = (
                    result.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
            else:
                pt = int(result.get("prompt_eval_count", 0))
                ct = int(result.get("eval_count", 0))
                if pt or ct:
                    token_usage.record(model, "emotion_evaluate", pt, ct)
                text = result.get("message", {}).get("content", "").strip()

            if text:
                self._parse(text)
        except Exception as exc:
            logger.debug("emotion evaluation failed: %s", exc)

    def get_context(self) -> str:
        """Format for prompt injection.  Returns ``""`` if both fields are empty."""
        if not self.tone and not self.motivation:
            return ""
        lines = []
        if self.tone:
            lines.append(f"情绪基调：{self.tone}")
        if self.motivation:
            lines.append(f"近期动机：{self.motivation}")
        return "【当前情绪】\n" + "\n".join(lines)

    # ── persistence ─────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.exists(self._path):
            self.tone = ""
            self.motivation = ""
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.tone = str(data.get("tone", "") or "")
            self.motivation = str(data.get("motivation", "") or "")
        except Exception as exc:
            logger.warning("failed to load emotion: %s", exc)
            self.tone = ""
            self.motivation = ""

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(
                    {"tone": self.tone, "motivation": self.motivation},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:
            logger.warning("failed to save emotion: %s", exc)

    def _parse(self, text: str) -> None:
        """Parse two-line LLM output::

            情绪基调：<value>
            近期动机：<value>
        """
        tone = ""
        motivation = ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("情绪基调"):
                val = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                if val and val not in ("（无）", "无"):
                    tone = val
            elif line.startswith("近期动机"):
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
