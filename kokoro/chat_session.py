"""Shared character chat session with memory and bounded history."""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

from kokoro import character
from kokoro import prompts

logger = logging.getLogger(__name__)


@dataclass
class ChatSession:
    character_id: str
    character_data: dict
    memory_backend: object
    max_history: int = 20
    history: list[dict] = field(default_factory=list)
    screen_contexts: list[str] = field(default_factory=list)
    max_screen_contexts: int = 3
    # Conversation summarization — compresses old history into a running summary
    max_window: int = 40        # max messages before triggering summarization
    compress_batch: int = 10    # oldest N messages to compress each time
    summary: str = ""
    summary_file: str = ""
    _summarize_in_progress: bool = False
    _summarize_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def character_name(self) -> str:
        return self.character_data["name"]

    @property
    def system_prompt(self) -> str:
        return character.build_system_prompt(self.character_data)

    @property
    def character_config(self) -> dict:
        """Per-character config from characters/{id}/config.toml.
        Re-reads from disk on every access for hot-reload during debugging."""
        return character.load_config(self.character_id)

    def add_screen_context(self, content: str) -> None:
        self.screen_contexts.append(content)
        if len(self.screen_contexts) > self.max_screen_contexts:
            self.screen_contexts = self.screen_contexts[-self.max_screen_contexts:]

    def load_summary(self) -> None:
        if not self.summary_file:
            return
        try:
            if os.path.exists(self.summary_file):
                with open(self.summary_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.summary = data.get("summary", "")
        except Exception as exc:
            logger.warning("failed to load summary: %s", exc)

    def save_summary(self) -> None:
        if not self.summary_file:
            return
        try:
            os.makedirs(os.path.dirname(self.summary_file), exist_ok=True)
            with open(self.summary_file, "w", encoding="utf-8") as f:
                json.dump({"summary": self.summary}, f, ensure_ascii=False)
        except Exception as exc:
            logger.warning("failed to save summary: %s", exc)

    def build_messages(
        self,
        user_text: str,
        include_screen: bool = True,
        extra_context: str | None = None,
        stt_refine_inline: bool = False,
        inject_memory: bool = True,
    ) -> list[dict]:
        messages = [{"role": "system", "content": self.system_prompt}]
        # History first — stable prefix for API prompt caching
        messages.extend(self.history)
        # Everything below varies per turn but sits AFTER history,
        # so the cache prefix (system prompt + history) stays intact.
        with self._summarize_lock:
            if self.summary:
                messages.append({"role": "system", "content": f"【对话摘要】\n{self.summary}"})
        if include_screen and self.screen_contexts:
            screen_text = prompts.get("chat_session.screen_context_prefix", "")
            for i, ctx in enumerate(self.screen_contexts, 1):
                screen_text += f"{i}. {ctx}\n"
            messages.append({"role": "system", "content": screen_text})
        if extra_context:
            messages.append({"role": "system", "content": extra_context})
        if inject_memory:
            memory_ctx = self.memory_backend.get_context(user_text, user_id=self.character_id)
            if memory_ctx:
                messages.append({"role": "system", "content": memory_ctx})
        if stt_refine_inline:
            inline_prompt = prompts.get("stt_refine_inline.system", "")
            if inline_prompt:
                messages.append({"role": "system", "content": inline_prompt})
        messages.append({"role": "user", "content": user_text})
        return messages

    def remember(self, user_text: str, assistant_text: str, async_store: bool = True) -> None:
        if not assistant_text:
            return
        # Skip tool-role messages — they are ephemeral conversation artifacts
        if user_text.startswith("[tool:") or assistant_text.startswith("[tool:"):
            return

        need_summary = False
        with self._summarize_lock:
            self.history.append({"role": "user", "content": user_text})
            self.history.append({"role": "assistant", "content": assistant_text})

            # Trigger summarization when history exceeds max_window
            if (
                not self._summarize_in_progress
                and len(self.history) > self.max_window
            ):
                batch = self.history[:self.compress_batch]
                self.history = self.history[self.compress_batch:]
                self._summarize_in_progress = True
                need_summary = True
            else:
                batch = None

        if need_summary and batch:
            threading.Thread(
                target=self._summarize_async,
                args=(batch,),
                daemon=True,
            ).start()

        if async_store:
            threading.Thread(
                target=self.memory_backend.store,
                args=(user_text, assistant_text, self.character_id),
                daemon=True,
            ).start()
        else:
            self.memory_backend.store(user_text, assistant_text, user_id=self.character_id)

    def _summarize_async(self, batch: list[dict]) -> None:
        try:
            conv_lines = []
            for msg in batch:
                role = "User" if msg["role"] == "user" else self.character_name
                conv_lines.append(f"{role}: {msg['content']}")
            conv_text = "\n".join(conv_lines)

            with self._summarize_lock:
                existing = self.summary

            new_summary = self._call_summary_llm(existing, conv_text)
            if new_summary:
                with self._summarize_lock:
                    self.summary = new_summary
                self.save_summary()
        except Exception as exc:
            logger.warning("conversation summarization failed: %s", exc)
        finally:
            self._summarize_in_progress = False

    def _call_summary_llm(self, existing_summary: str, conversation: str) -> str | None:
        prompt = prompts.format_prompt(
            "conversation_summary.user_template",
            existing_summary=existing_summary or "无",
            conversation=conversation,
        )
        system = prompts.get("conversation_summary.system", "")

        from kokoro import config as cfg
        from kokoro import token_usage

        model = cfg.stt_refine_model()
        url = cfg.llm_url()
        api_key = ""
        if cfg.is_deepseek_model(model):
            api_key = cfg.deepseek_api_key()
            url = cfg.deepseek_url()

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            api_url = f"{url}/v1/chat/completions"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 512,
            }
        else:
            api_url = f"{url}/api/chat"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 512},
            }

        try:
            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
            if api_key:
                usage = result.get("usage", {})
                pt = int(usage.get("prompt_tokens", 0))
                ct = int(usage.get("completion_tokens", 0))
                if pt or ct:
                    token_usage.record(model, "conversation_summary", pt, ct)
                text = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            else:
                pt = int(result.get("prompt_eval_count", 0))
                ct = int(result.get("eval_count", 0))
                if pt or ct:
                    token_usage.record(model, "conversation_summary", pt, ct)
                text = result.get("message", {}).get("content", "").strip()
            return text if text else None
        except Exception as exc:
            logger.warning("summary LLM call failed: %s", exc)
            return None


def load_session(
    character_id: str,
    memory_backend: object,
    max_history: int = 20,
) -> ChatSession:
    characters = character.load()
    if character_id not in characters:
        raise KeyError(character_id)
    return ChatSession(
        character_id=character_id,
        character_data=characters[character_id],
        memory_backend=memory_backend,
        max_history=max_history,
    )


def inject_memory_context(
    messages: list[dict],
    memory_context: Optional[str],
) -> list[dict]:
    if not memory_context:
        return messages

    result = list(messages)
    sys_indices = [i for i, msg in enumerate(result) if msg.get("role") == "system"]
    pos = sys_indices[-1] + 1 if sys_indices else 0
    result.insert(pos, {"role": "system", "content": memory_context})
    return result


def last_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content", "")
    return ""


def store_memory_async(memory_backend: object, user_text: str, assistant_text: str, user_id: str) -> None:
    if not user_text or not assistant_text:
        return
    threading.Thread(
        target=memory_backend.store,
        args=(user_text, assistant_text, user_id),
        daemon=True,
    ).start()
