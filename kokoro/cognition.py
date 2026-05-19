"""Cognition layer: stable perceptions about people, relationships, and things.

Full data lives in ``characters/{id}/cognition.json``. Runtime cache is a
small, locally selected subset that is injected into chat and dialogue prompts.
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

_PRIORITY_KEYS = {"自己", "自我"}
_RELATION_MARKERS = ("关系", "和自己")
_SHORT_LIVED_KEYWORDS = (
    "当前", "今天", "今晚", "明天", "页面", "网页", "屏幕", "计划", "日程",
    "刚才", "这次", "本次", "临时", "弹幕", "直播间当前",
)
_BAD_KEY_CHARS_RE = re.compile(r"[()（）:：\[\]【】]")

_GENERIC_PERSON_KEYS = {
    "\u7528\u6237",
    "\u73a9\u5bb6",
    "\u5bf9\u65b9",
    "\u4eba\u7c7b",
    "\u4e3b\u4eba",
    "\u81ea\u5df1\u548c\u7528\u6237\u7684\u5173\u7cfb",
    "\u7528\u6237\u548c\u81ea\u5df1\u7684\u5173\u7cfb",
    "\u81ea\u5df1\u548c\u73a9\u5bb6\u7684\u5173\u7cfb",
    "\u73a9\u5bb6\u548c\u81ea\u5df1\u7684\u5173\u7cfb",
    "\u81ea\u5df1\u548c\u5bf9\u65b9\u7684\u5173\u7cfb",
    "\u5bf9\u65b9\u548c\u81ea\u5df1\u7684\u5173\u7cfb",
}

_GENERIC_PERSON_PREFIXES = (
    "用户和自己",
    "自己和用户",
    "对方和自己",
    "自己和对方",
    "观众和自己",
    "自己和观众",
)


class CognitionStore:
    """Full cognition data + runtime cache manager."""

    def __init__(self, character_id: str, character_data: dict | None = None):
        self.character_id = character_id
        self._path = os.path.join(_CHARACTERS_DIR, character_id, "cognition.json")
        self._entries: dict[str, str] = {}
        self._cache: dict[str, str] = {}
        self._load_or_seed(character_data)

    def refresh_cache(self, user_text: str, assistant_text: str = "") -> None:
        combined = f"{user_text} {assistant_text}"
        matched: dict[str, str] = {}
        for key, value in self._entries.items():
            if _key_matches(key, combined):
                matched[key] = value
        _ensure(self._entries, matched, _PRIORITY_KEYS)
        _ensure(self._entries, matched, {k for k in self._entries if _is_relation_key(k)})
        # Keep a small self-anchor even when the current turn does not mention it explicitly.
        _ensure(self._entries, matched, {k for k in self._entries if k in {"自己", "自我"}})
        self._cache = matched

    def evaluate(
        self,
        conversation: str,
        summary: str,
        memories: str,
        character_name: str,
        character_id: str,
        user_name: str = "你",
    ) -> None:
        from kokoro import config as cfg
        from kokoro import prompts
        from kokoro import token_usage

        existing = json.dumps(self._entries, ensure_ascii=False, indent=2)
        debug = {
            "system_prompt": "",
            "user_prompt": "",
            "raw_response": "",
            "parsed_entries": None,
            "saved": False,
            "error": "",
        }
        system_prompt = prompts.format_prompt("cognition.evaluate_system", name=character_name, user_name=user_name)
        user_prompt = prompts.format_prompt(
            "cognition.evaluate_user",
            existing=existing,
            conversation=conversation,
            summary=summary,
            memories=memories,
            name=character_name,
            user_name=user_name,
        )
        debug["system_prompt"] = system_prompt
        debug["user_prompt"] = user_prompt

        model = cfg.cognition_model() or cfg.llm_model()
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
                "temperature": 0.2,
                "max_tokens": 2048,
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
                "options": {"temperature": 0.2, "num_predict": 2048},
            }

        try:
            import urllib.request

            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())

            if openai_compatible:
                usage = result.get("usage", {})
                pt = int(usage.get("prompt_tokens", 0))
                ct = int(usage.get("completion_tokens", 0))
                if pt or ct:
                    token_usage.record(model, "cognition_evaluate", pt, ct)
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
                    token_usage.record(model, "cognition_evaluate", pt, ct)
                text = result.get("message", {}).get("content", "").strip()

            new_entries = self._parse_json_entries(text)
            debug["raw_response"] = text
            debug["parsed_entries"] = new_entries
            if new_entries is not None:
                if _looks_destructive(self._entries, new_entries):
                    logger.warning(
                        "cognition evaluation rejected destructive update: %d -> %d entries",
                        len(self._entries),
                        len(new_entries),
                    )
                    return
                self._entries = new_entries
                self._save()
                debug["saved"] = True
                logger.info("cognition evaluated: %d entries", len(self._entries))
        except Exception as exc:
            debug["error"] = str(exc)
            logger.warning("cognition evaluation failed: %s", exc)
        return debug

    def get_context(self) -> str:
        if not self._cache:
            return ""
        lines = [f"- {key}: {value}" for key, value in self._cache.items()]
        return "【认知】\n" + "\n".join(lines)

    def _load_or_seed(self, character_data: dict | None) -> None:
        if os.path.exists(self._path):
            self._load()
        elif character_data:
            self._seed(character_data)
            self._save()

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._entries = _validate_entries(data.get("entries", {})) or {}
        except Exception as exc:
            logger.warning("failed to load cognition: %s", exc)
            self._entries = {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump({"entries": self._entries}, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("failed to save cognition: %s", exc)

    def _seed(self, data: dict) -> None:
        seeds: dict[str, str] = {}
        name = str(data.get("name") or "角色")
        rel = str(data.get("relationship") or "")
        if rel:
            seeds[f"{name}和自己的关系"] = rel
        personality = str(data.get("personality") or "")
        background = str(data.get("background") or "")
        self_text = " ".join(part for part in (personality, background) if part).strip()
        if self_text:
            seeds["自己"] = self_text[:300]
        self._entries = _validate_entries(seeds) or {}

    @staticmethod
    def _parse_json_entries(text: str) -> dict[str, str] | None:
        stripped = text.strip()
        m = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
        if m:
            stripped = m.group(1).strip()
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            logger.debug("cognition: could not parse LLM response: %s", text[:200])
            return None
        if isinstance(data, dict) and "entries" in data:
            return _validate_entries(data["entries"])
        if isinstance(data, dict):
            return _validate_entries(data)
        return None


def _validate_entries(raw) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        clean_key = _normalize_key(key)
        clean_value = _normalize_value(value)
        if not clean_key or not clean_value:
            continue
        if clean_key in _GENERIC_PERSON_KEYS:
            continue
        if clean_key.startswith(_GENERIC_PERSON_PREFIXES):
            continue
        if _is_short_lived_key(clean_key):
            continue
        if _BAD_KEY_CHARS_RE.search(clean_key):
            continue
        result[clean_key] = clean_value
    return result if result else None


def _normalize_key(key: str) -> str:
    key = key.strip()
    key = re.sub(r"\s+", "", key)
    key = key.replace("与", "和")
    return key[:30]


def _normalize_value(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip())
    return value[:260]


def _is_short_lived_key(key: str) -> bool:
    return any(word in key for word in _SHORT_LIVED_KEYWORDS)


def _is_relation_key(key: str) -> bool:
    return any(marker in key for marker in _RELATION_MARKERS)


def _key_matches(key: str, text: str) -> bool:
    if not key or not text:
        return False
    if key in text:
        return True
    if _is_relation_key(key):
        for marker in _RELATION_MARKERS:
            if marker in key:
                subject = key.split(marker, 1)[0]
                return bool(subject and subject in text)
    return False


def _ensure(source: dict[str, str], target: dict[str, str], keys: set[str]) -> None:
    for key in keys:
        if key in source:
            target[key] = source[key]


def _looks_destructive(old: dict[str, str], new: dict[str, str]) -> bool:
    if len(old) < 3:
        return False
    if len(new) == 0:
        return True
    if len(new) < max(2, len(old) // 3):
        protected_old = {k for k in old if k in _PRIORITY_KEYS or _is_relation_key(k)}
        protected_new = protected_old.intersection(new)
        return len(protected_new) < max(1, len(protected_old) // 2)
    return False
