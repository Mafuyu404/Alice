"""Cognition layer — evolving perceptions about people, relationships, and things.

Architecture
------------
Full data (cognition.json) stores ALL perception entries and is only updated
during context summarization (an LLM call evaluates new conversations against
existing entries).

Runtime cache holds a SUBSET of entries relevant to the current conversation,
refreshed after each turn by simple keyword matching (no LLM).  ``build_messages``
injects the cache, never the full data, keeping the prompt focused.
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

# Keys always kept in the runtime cache (self-perception, relationship anchors)
_PRIORITY_KEYS = {"自己", "自己和"}


class CognitionStore:
    """全量认知数据 + 运行时缓存的管理器。

    - ``_entries``: 全量数据 (keyword → text)，只在 summarization 时更新
    - ``_cache``:   运行时缓存，每次对话后从全量中筛选当前相关条目
    """

    def __init__(self, character_id: str, character_data: dict | None = None):
        self.character_id = character_id
        self._path = os.path.join(_CHARACTERS_DIR, character_id, "cognition.json")
        self._entries: dict[str, str] = {}
        self._cache: dict[str, str] = {}
        self._load_or_seed(character_data)

    # ── public API ──────────────────────────────────────────────────────────

    def refresh_cache(self, user_text: str, assistant_text: str = "") -> None:
        """After each conversation turn, select relevant entries by keyword matching.

        No LLM involved — pure substring matching against entry keys.
        Priority entries (self-perception, relationship anchors) are always kept.
        """
        combined = user_text + " " + assistant_text
        matched: dict[str, str] = {}

        for key, value in self._entries.items():
            if combined and key in combined:
                matched[key] = value

        # Always carry priority entries
        _ensure(self._entries, matched, _PRIORITY_KEYS)
        # Everything whose key implies a relationship perception
        _ensure(self._entries, matched, {
            k for k in self._entries if "和" in k or "关系" in k
        })

        self._cache = matched

    def evaluate(
        self,
        conversation: str,
        summary: str,
        memories: str,
        character_name: str,
        character_id: str,
    ) -> None:
        """Full evaluation during context summarisation.

        Calls an LLM to reconcile existing entries with new conversation,
        then persists the result and refreshes the cache.
        """
        from kokoro import config as cfg
        from kokoro import prompts as _prompts
        from kokoro import token_usage

        existing = json.dumps(self._entries, ensure_ascii=False, indent=2)

        system_prompt = (
            f"你是{character_name}的认知系统。你的职责是根据最新对话内容，"
            f"更新{character_name}对周围事物、人和关系的认知。"
        )

        user_prompt = (
            f"现有认知条目（JSON格式）：\n{existing}\n\n"
            f"最近对话内容：\n{conversation}\n\n"
            f"对话摘要：\n{summary}\n\n"
            f"相关长期记忆：\n{memories}\n\n"
            "请分析以上信息，输出更新后的认知条目。\n\n"
            "要求：\n"
            "1. 根据新对话修正已有认知，删除已被新信息覆盖的过时条目\n"
            "2. 对新话题建立新条目\n"
            "3. 【最重要】重点关注对人的认知、对自身与他人关系的认知\n"
            "4. 条目数量不限，有多少写多少，不需要刻意合并或精简\n\n"
            "输出格式为JSON：\n"
            '{"entries": {"关键词": "认知描述", ...}}'
        )

        model = cfg.cognition_model() or cfg.stt_refine_model()
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
            base_url = url.rstrip("/")
            import re as _re
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
                "options": {"temperature": 0.3, "num_predict": 2048},
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
            if new_entries is not None:
                self._entries = new_entries
                self._save()
                logger.info(
                    "cognition evaluated: %d entries", len(self._entries)
                )
        except Exception as exc:
            logger.warning("cognition evaluation failed: %s", exc)

    def get_context(self) -> str:
        """Format cache entries as a system-context block for prompt injection.

        Returns an empty string when cache is empty.
        """
        if not self._cache:
            return ""
        lines: list[str] = []
        for key, value in self._cache.items():
            lines.append(f"- {key}：{value}")
        return "【认知】\n" + "\n".join(lines)

    # ── persistence ─────────────────────────────────────────────────────────

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
            self._entries = data.get("entries", {})
        except Exception as exc:
            logger.warning("failed to load cognition: %s", exc)
            self._entries = {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(
                    {"entries": self._entries},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:
            logger.warning("failed to save cognition: %s", exc)

    def _seed(self, data: dict) -> None:
        """Initial population from character definition (本心层 → 认知层 migration)."""
        seeds: dict[str, str] = {}

        rel = data.get("relationship", "")
        if rel and "住在一起" in rel:
            seeds["自己和%s的关系" % data.get("name", "对方")] = rel

        bg = data.get("background", "")
        if bg:
            self_parts = []
            for phrase in ["头脑聪明", "博览群书", "在很多领域都能聊上几句"]:
                if phrase in bg:
                    self_parts.append(phrase)
            if self_parts:
                seeds["自己"] = "，".join(self_parts) + "。"

        pf = data.get("personality", "")
        if pf:
            self_parts = []
            for phrase in ["情感表达不算外放", "关心都藏在行动里", "偶尔会一本正经地调侃一句"]:
                if phrase in pf:
                    self_parts.append(phrase)
            if self_parts:
                existing = seeds.get("自己", "")
                extra = "，".join(self_parts) + "。"
                seeds["自己"] = (existing + extra) if existing else extra

        self._entries = seeds if seeds else {}

    @staticmethod
    def _parse_json_entries(text: str) -> dict[str, str] | None:
        """Parse LLM JSON output, trying several recovery strategies."""
        import re as _re

        stripped = text.strip()

        # Strategy 1: markdown code block
        m = _re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, _re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1).strip())
                if isinstance(data, dict) and "entries" in data:
                    return _validate_entries(data["entries"])
            except json.JSONDecodeError:
                pass

        # Strategy 2: raw JSON
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                if "entries" in data:
                    return _validate_entries(data["entries"])
                # Maybe the whole object IS entries
                return _validate_entries(data)
        except json.JSONDecodeError:
            pass

        logger.debug("cognition: could not parse LLM response: %s", text[:200])
        return None


def _validate_entries(raw) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    result: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
            result[k.strip()] = v.strip()
    return result if result else None


def _ensure(
    source: dict[str, str],
    target: dict[str, str],
    keys: set[str],
) -> None:
    for k in keys:
        if k in source:
            target[k] = source[k]
