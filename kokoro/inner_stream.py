"""Inner narrative stream for self-expression.

The stream is intentionally plain text.  Runtime code may read, write, and
inject it, but must not parse it into rules or scores.
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


class InnerStream:
    """A character's current inner continuity, maintained by an LLM."""

    def __init__(self, character_id: str, character_data: dict | None = None):
        self.character_id = character_id
        self.character_data = character_data or {}
        self._path = os.path.join(_CHARACTERS_DIR, character_id, "inner_stream.txt")
        self.text: str = ""
        self._load()

    def get_context(self) -> str:
        if not self.text.strip():
            return ""
        return "【内在叙事流】\n" + self.text.strip()

    def evaluate(
        self,
        *,
        user_text: str,
        assistant_text: str,
        character_name: str,
        user_name: str,
        summary: str = "",
        recent_history: str = "",
        cognition_context: str = "",
        emotion_context: str = "",
        memory_context: str = "",
        scene_context: str = "",
    ) -> dict:
        """Rewrite the stream after a meaningful turn.

        This returns debug data for tests/tools.  Failures are non-fatal.
        """
        from kokoro import config as cfg
        from kokoro import prompts
        from kokoro import token_usage

        debug = {
            "system_prompt": "",
            "user_prompt": "",
            "raw_response": "",
            "before": self.text,
            "after": self.text,
            "saved": False,
            "error": "",
        }

        section = cfg.inner_stream_config()
        if not section.get("enabled", True):
            return debug

        system_prompt = prompts.format_prompt(
            "inner_stream.evaluate_system",
            name=character_name,
            user_name=user_name,
        )
        profile = _compact_profile(self.character_data)
        user_prompt = prompts.format_prompt(
            "inner_stream.evaluate_user",
            name=character_name,
            user_name=user_name,
            current=self.text.strip() or "（空）",
            profile=profile or "（无）",
            summary=summary or "（无）",
            recent_history=recent_history or "（无）",
            cognition_context=cognition_context or "（无）",
            emotion_context=emotion_context or "（无）",
            memory_context=memory_context or "（无）",
            scene_context=scene_context or "（无）",
            user_text=user_text or "（无）",
            assistant_text=assistant_text or "（无）",
        )
        debug["system_prompt"] = system_prompt
        debug["user_prompt"] = user_prompt

        model = str(section.get("model") or "").strip() or cfg.llm_model()
        url = cfg.llm_url()
        api_key = ""
        openai_compatible = False
        if cfg.is_deepseek_model(model):
            api_key = cfg.deepseek_api_key()
            url = cfg.deepseek_url()
            openai_compatible = True

        headers = {"Content-Type": "application/json"}
        max_tokens = int(section.get("max_tokens", 700) or 700)
        try:
            import urllib.request

            if openai_compatible:
                headers["Authorization"] = f"Bearer {api_key}"
                base_url = url.rstrip("/")
                if not re.search(r"/v\d+$", base_url):
                    base_url += "/v1"
                api_url = f"{base_url}/chat/completions"
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.5,
                    "max_tokens": max_tokens,
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
                    "options": {"temperature": 0.5, "num_predict": max_tokens},
                }

            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read())

            if openai_compatible:
                usage = result.get("usage", {})
                pt = int(usage.get("prompt_tokens", 0))
                ct = int(usage.get("completion_tokens", 0))
                if pt or ct:
                    token_usage.record(model, "inner_stream_evaluate", pt, ct)
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
                    token_usage.record(model, "inner_stream_evaluate", pt, ct)
                text = result.get("message", {}).get("content", "").strip()

            debug["raw_response"] = text
            cleaned = _clean_stream_text(text, max_chars=int(section.get("max_chars", 1200) or 1200))
            if cleaned and _looks_complete(cleaned):
                self.text = cleaned
                self._save()
                debug["after"] = self.text
                debug["saved"] = True
        except Exception as exc:
            debug["error"] = str(exc)
            logger.debug("inner stream evaluation failed: %s", exc)
        return debug

    def _load(self) -> None:
        if not os.path.exists(self._path):
            self.text = ""
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self.text = _clean_stream_text(f.read(), max_chars=1600)
        except Exception as exc:
            logger.warning("failed to load inner stream: %s", exc)
            self.text = ""

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                f.write(self.text.strip() + "\n")
        except Exception as exc:
            logger.warning("failed to save inner stream: %s", exc)


def _clean_stream_text(text: str, *, max_chars: int) -> str:
    text = re.sub(r"```(?:text|markdown)?\s*\n?(.*?)```", r"\1", str(text), flags=re.DOTALL)
    text = text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:max(200, max_chars)].strip()


def _looks_complete(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 40:
        return False
    if stripped.endswith(("这", "那", "但", "而", "因为", "所以", "如果", "不是", "可以", "一个")):
        return False
    return True


def _compact_profile(data: dict) -> str:
    parts: list[str] = []
    for key in ("name", "description", "personality", "background", "relationship"):
        value = str(data.get(key, "") or "").strip()
        if value:
            parts.append(f"{key}: {value[:500]}")
    return "\n\n".join(parts)
