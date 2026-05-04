"""Shared character chat session with memory and bounded history."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from kokoro import character


@dataclass
class ChatSession:
    character_id: str
    character_data: dict
    memory_backend: object
    max_history: int = 20
    history: list[dict] = field(default_factory=list)
    screen_contexts: list[str] = field(default_factory=list)
    max_screen_contexts: int = 3

    @property
    def character_name(self) -> str:
        return self.character_data["name"]

    @property
    def system_prompt(self) -> str:
        return character.build_system_prompt(self.character_data)

    def add_screen_context(self, content: str) -> None:
        self.screen_contexts.append(content)
        if len(self.screen_contexts) > self.max_screen_contexts:
            self.screen_contexts = self.screen_contexts[-self.max_screen_contexts:]

    def build_messages(self, user_text: str, include_screen: bool = True) -> list[dict]:
        messages = [{"role": "system", "content": self.system_prompt}]
        if include_screen and self.screen_contexts:
            screen_text = "以下是你最近通过屏幕识别观察到的用户活动（按时间顺序从早到晚）：\n"
            for i, ctx in enumerate(self.screen_contexts, 1):
                screen_text += f"{i}. {ctx}\n"
            messages.append({"role": "system", "content": screen_text})
        memory_ctx = self.memory_backend.get_context(user_text, user_id=self.character_id)
        if memory_ctx:
            messages.append({"role": "system", "content": memory_ctx})
        messages.extend(self.history)
        messages.append({"role": "user", "content": user_text})
        return messages

    def remember(self, user_text: str, assistant_text: str, async_store: bool = True) -> None:
        if not assistant_text:
            return

        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": assistant_text})
        limit = self.max_history * 2
        if len(self.history) > limit:
            self.history[:] = self.history[-limit:]

        if async_store:
            threading.Thread(
                target=self.memory_backend.store,
                args=(user_text, assistant_text, self.character_id),
                daemon=True,
            ).start()
        else:
            self.memory_backend.store(user_text, assistant_text, user_id=self.character_id)


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
