"""Cognition layer: stable perceptions about people, relationships, and things.

Full data lives in ``characters/{id}/cognition.json``. Runtime cache is a
small, locally selected subset that is injected into chat and impulse prompts.
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

_PRIORITY_KEYS = {"Alice", "爱丽丝", "自己", "自我", "真冬", "真冬和自己的关系"}
_RELATION_MARKERS = ("关系", "和自己", "和Alice", "和爱丽丝")
_SHORT_LIVED_KEYWORDS = (
    "当前", "今天", "今晚", "明天", "页面", "网页", "屏幕", "计划", "日程",
    "刚才", "这次", "本次", "临时", "弹幕", "直播间当前",
)
_BAD_KEY_CHARS_RE = re.compile(r"[()（）:：\[\]【】]")


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
        self._cache = matched

    def evaluate(
        self,
        conversation: str,
        summary: str,
        memories: str,
        character_name: str,
        character_id: str,
    ) -> None:
        from kokoro import config as cfg
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
        system_prompt = (
            f"你是{character_name}的认知层维护器。你的任务是维护长期、稳定、可复用的认知条目，"
            "而不是记录临时页面、一次性计划或对话流水账。"
        )
        user_prompt = (
            f"现有认知条目（JSON）：\n{existing}\n\n"
            f"最近对话内容：\n{conversation}\n\n"
            f"对话摘要：\n{summary}\n\n"
            f"相关长期记忆：\n{memories}\n\n"
            "请结合以上信息输出更新后的完整认知 JSON。\n\n"
            "认知层定义：\n"
            "- 记录 Alice 对确定存在的人、关系、自我、长期事物、游戏、作品、项目的稳定认知。\n"
            "- 它会长期影响之后的态度、措辞和解释方式。\n"
            "- 它不是长期记忆流水账，也不是当前页面缓存。\n\n"
            "最重要规则：\n"
            "1. 对任何确定存在的人，尽量建立单独条目。key 直接用姓名或昵称，例如“真冬”“某观众昵称”。\n"
            "2. 关系也单独建条目，例如“真冬和自己的关系”。\n"
            "3. 直播观众如果有稳定昵称和可归纳特征，也应单独建条目，不要合并成笼统的“观众”。\n"
            "4. 如果同一批对话介绍了多个不同的人或多个不同游戏/作品/项目，必须尽量分别保留独立 key，不要只总结成一个泛化条目。\n"
            "5. 已有有效条目默认保留。只有明确被新信息纠正、过期、短期污染或 key 不合格时才删除；不要因为本轮没提到就删除。\n"
            "6. key 必须简单可匹配。禁止括号、冒号、方括号、长句、日期、临时限定词。\n"
            "7. 禁止写入当前页面、当前网页、今天计划、今晚安排、刚才的弹幕、一次性任务、临时屏幕内容。\n"
            "8. Edge 页面和屏幕内容只能作为判断材料；只有反复出现、具有长期意义的事物才能沉淀，例如“我的世界”“Minecraft模组”“自动化”。\n"
            "9. value 要短而密，1 到 3 句，描述稳定偏好、印象、关系、说话风格或未来对话态度。\n"
            "10. 删除或改写已过期、短期、无法匹配、带括号的旧条目。\n"
            "11. 宁可少写，也不要把临时上下文写进 cognition。\n\n"
            "批量实体要求：\n"
            "- 人物：如果出现“某人叫X / X经常 / X喜欢 / X总是 / X是观众”等确定描述，应建立 key“X”。\n"
            "- 游戏/作品/项目：如果出现“游戏X / X是一款 / X的核心是 / X适合”等确定描述，应建立 key“X”。\n"
            "- 不要把十个人压缩成“观众”，不要把十个游戏压缩成“游戏”。\n\n"
            "硬性自检：\n"
            "- 输出前先在心里列出最近对话中所有确定存在的人名/昵称，逐一检查是否都有独立 key。\n"
            "- 输出前先在心里列出最近对话中所有确定存在的游戏/作品/项目名，逐一检查是否都有独立 key。\n"
            "- 如果对话明确介绍了 5 个不同的人，完整 JSON 中至少应包含这 5 个具体人名 key。\n"
            "- 如果对话明确介绍了 5 个不同游戏/作品/项目，完整 JSON 中至少应包含这 5 个具体对象名 key。\n"
            "- 泛化条目可以作为补充，但不能替代具体人名、昵称、游戏名、作品名。\n"
            "- 不要输出自检过程，只输出最终 JSON。\n\n"
            "好 key 示例：真冬、真冬和自己的关系、Alice、我的世界、Minecraft模组、自动化、某观众昵称。\n"
            "坏 key 示例：真冬（当前互动对象）、观众：历史互动、当前页面、今天的计划、FTB NeoTech页面。\n\n"
            "只输出 JSON，不要解释。格式：\n"
            "{\"entries\": {\"简单key\": \"长期认知文本\"}}\n"
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
        name = str(data.get("name") or "Alice")
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
