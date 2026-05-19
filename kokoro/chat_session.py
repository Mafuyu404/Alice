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
from kokoro import memory as memory_mod
from kokoro import prompts
from kokoro import scene as scene_mod

logger = logging.getLogger(__name__)


@dataclass
class ChatSession:
    character_id: str
    character_data: dict
    memory_backend: object
    user_name: str = "你"
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
    # Three-layer personality system
    cognition: object = field(default=None)   # CognitionStore instance
    emotion: object = field(default=None)      # EmotionState instance
    inner_stream: object = field(default=None)  # InnerStream instance
    # Periodic cognition evaluation (every N conversation turns)
    cognition_eval_interval: int = 5
    _cognition_turn_counter: int = 0
    # Memory event store (structured event extraction)
    memory_events: object = field(default=None)  # MemoryEventStore
    # Scene type — determines information source layout and guidance prompt
    _scene: object = field(default=None)  # scene_mod.SceneType | None
    # Config overrides — applied on top of the per-character config.toml
    # Used by relay mode to force different model/URL without touching disk
    _config_overrides: dict = field(default_factory=dict)
    memory_counterpart: str = ""

    def _character_config(self) -> dict:
        """Disk config merged with overrides."""
        cfg = character.load_config(self.character_id)
        cfg.update(self._config_overrides)
        return cfg

    def __post_init__(self) -> None:
        from kokoro.cognition import CognitionStore
        from kokoro.emotion import EmotionState
        from kokoro.inner_stream import InnerStream
        from kokoro.memory_events import MemoryEventStore
        if self.cognition is None:
            self.cognition = CognitionStore(self.character_id, self.character_data)
        if self.emotion is None:
            self.emotion = EmotionState(self.character_id)
        if self.inner_stream is None:
            self.inner_stream = InnerStream(self.character_id, self.character_data)
        if self.memory_events is None:
            self.memory_events = MemoryEventStore(self.memory_backend, self.character_id)
        if self._scene is None:
            from kokoro import config as _cfg
            self._scene = scene_mod.resolve(_cfg.load())

    @property
    def character_name(self) -> str:
        return self.character_data["name"]

    @property
    def system_prompt(self) -> str:
        return character.build_system_prompt(self.character_data, user_name=self.user_name)

    @property
    def scene_name(self) -> str:
        """Human-readable scene name for the guidance prefix."""
        return scene_mod.scene_name(self._scene)

    @property
    def scene_guidance(self) -> str:
        """Scene guidance block describing information sources."""
        from kokoro import config as _cfg
        return scene_mod.guidance_text(self._scene, self.user_name, self.character_name, _cfg.load())

    @property
    def character_config(self) -> dict:
        """Per-character config from characters/{id}/config.toml + overrides."""
        return self._character_config()

    def add_screen_context(self, content: str) -> None:
        self.screen_contexts.append(content)
        if len(self.screen_contexts) > self.max_screen_contexts:
            self.screen_contexts = self.screen_contexts[-self.max_screen_contexts:]

    def set_memory_counterpart(self, name: str) -> None:
        self.memory_counterpart = str(name or "").strip()

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
        # Scene guidance — describes the information source layout
        guidance = self.scene_guidance
        if guidance:
            prefix = prompts.get("scene.prefix", "【当前场景：{scene_name}】")
            scene_label = prefix.format(scene_name=self.scene_name)
            messages.append({"role": "system", "content": f"{scene_label}\n{guidance}"})
        if include_screen and self.screen_contexts:
            screen_text = prompts.get("chat_session.screen_context_prefix", "")
            for i, ctx in enumerate(self.screen_contexts, 1):
                screen_text += f"{i}. {ctx}\n"
            messages.append({"role": "system", "content": screen_text})
        if extra_context:
            messages.append({"role": "system", "content": extra_context})
        if inject_memory:
            memory_ctx = self.memory_backend.get_context_multi(
                user_text,
                memory_mod.context_user_ids(self.character_id, self.memory_counterpart or self.user_name),
            )
            if memory_ctx:
                messages.append({"role": "system", "content": memory_ctx})
        # Cognitive layer — runtime cache of relevant perceptions
        cognition_ctx = self.cognition.get_context()
        if cognition_ctx:
            messages.append({"role": "system", "content": cognition_ctx})

        # Inner stream — plain-text continuity for self-expression.
        inner_ctx = self.inner_stream.get_context()
        if inner_ctx:
            messages.append({"role": "system", "content": inner_ctx})

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

        # Memory event extraction (replaces raw conversation-pair storage)
        if self.memory_events is not None:
            with self._summarize_lock:
                summary = self.summary
            threading.Thread(
                target=self.memory_events.on_conversation_turn,
                args=(
                    user_text,
                    assistant_text,
                    self.user_name,
                    self.character_name,
                    summary,
                    self.memory_counterpart or self.user_name,
                ),
                daemon=True,
            ).start()

        # Refresh cognition cache — keyword match against current turn, no LLM
        self.cognition.refresh_cache(user_text, assistant_text)

        # Async emotion evaluation — LLM-based, non-blocking
        threading.Thread(
            target=self.emotion.evaluate,
            args=(user_text, assistant_text, self.character_name, self.user_name),
            daemon=True,
        ).start()

        # Inner stream updates synchronously so the next turn immediately sees it.
        self._eval_inner_stream_async(user_text, assistant_text)

        # Periodic cognition evaluation (every N turns, independent of summary)
        self._cognition_turn_counter += 1
        if self.cognition_eval_interval > 0 and self._cognition_turn_counter >= self.cognition_eval_interval:
            self._cognition_turn_counter = 0
            threading.Thread(
                target=self._eval_cognition_async,
                args=(user_text, assistant_text),
                daemon=True,
            ).start()

    def _summarize_async(self, batch: list[dict]) -> None:
        try:
            conv_lines = []
            for msg in batch:
                role = self.user_name if msg["role"] == "user" else self.character_name
                conv_lines.append(f"{role}：{msg['content']}")
            conv_text = "\n".join(conv_lines)

            with self._summarize_lock:
                existing = self.summary

            new_summary = self._call_summary_llm(existing, conv_text)
            if new_summary:
                with self._summarize_lock:
                    self.summary = new_summary
                self.save_summary()

                # Cognition full evaluation after summarization
                try:
                    memories = self.memory_backend.get_context_multi(
                        conv_text[:500],
                        memory_mod.context_user_ids(self.character_id, self.memory_counterpart or self.user_name),
                    )
                    self.cognition.evaluate(
                        conversation=conv_text,
                        summary=new_summary,
                        memories=memories or "",
                        character_name=self.character_name,
                        character_id=self.character_id,
                        user_name=self.user_name,
                    )
                except Exception as cexc:
                    logger.warning("cognition evaluation failed: %s", cexc)
        except Exception as exc:
            logger.warning("conversation summarization failed: %s", exc)
        finally:
            self._summarize_in_progress = False

    def _eval_cognition_async(self, user_text: str, assistant_text: str) -> None:
        """Periodic lightweight cognition evaluation (no summary needed)."""
        try:
            conv_text = f"{self.user_name}：{user_text}\n{self.character_name}：{assistant_text}"
            memories = self.memory_backend.get_context_multi(
                conv_text[:500],
                memory_mod.context_user_ids(self.character_id, self.memory_counterpart or self.user_name),
            )
            self.cognition.evaluate(
                conversation=conv_text,
                summary=self.summary or "",
                memories=memories or "",
                character_name=self.character_name,
                character_id=self.character_id,
                user_name=self.user_name,
            )
        except Exception as exc:
            logger.warning("periodic cognition evaluation failed: %s", exc)

    def _eval_inner_stream_async(self, user_text: str, assistant_text: str) -> None:
        try:
            recent = _format_recent_history(
                self.history[-10:],
                user_name=self.user_name,
                character_name=self.character_name,
            )
            try:
                memories = self.memory_backend.get_context_multi(
                    f"{user_text} {assistant_text}"[:500],
                    memory_mod.context_user_ids(self.character_id, self.memory_counterpart or self.user_name),
                )
            except Exception:
                memories = ""
            self.inner_stream.evaluate(
                user_text=user_text,
                assistant_text=assistant_text,
                character_name=self.character_name,
                user_name=self.user_name,
                summary=self.summary or "",
                recent_history=recent,
                cognition_context=self.cognition.get_context() if self.cognition else "",
                emotion_context=self.emotion.get_context() if self.emotion else "",
                memory_context=memories or "",
                scene_context=self.scene_guidance or "",
            )
        except Exception as exc:
            logger.warning("inner stream evaluation failed: %s", exc)

    def _call_summary_llm(self, existing_summary: str, conversation: str) -> str | None:
        prompt = prompts.format_prompt(
            "conversation_summary.user_template",
            existing_summary=existing_summary or "无",
            conversation=conversation,
            user_name=self.user_name,
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
    cognition_eval_interval: int | None = None,
) -> ChatSession:
    from kokoro import config as cfg
    characters = character.load()
    if character_id not in characters:
        raise KeyError(character_id)
    if cognition_eval_interval is None:
        cognition_eval_interval = cfg.cognition_eval_interval()
    return ChatSession(
        character_id=character_id,
        character_data=characters[character_id],
        memory_backend=memory_backend,
        user_name=cfg.user_name(),
        max_history=max_history,
        cognition_eval_interval=cognition_eval_interval,
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


def _format_recent_history(messages: list[dict], *, user_name: str, character_name: str) -> str:
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "user":
            speaker = user_name
        elif role == "assistant":
            speaker = character_name
        else:
            speaker = "system"
        content = str(msg.get("content", "") or "").strip()
        if content:
            lines.append(f"{speaker}：{content[:500]}")
    return "\n".join(lines)


def store_memory_async(memory_backend: object, user_text: str, assistant_text: str, user_id: str) -> None:
    if not user_text or not assistant_text:
        return
    threading.Thread(
        target=memory_backend.store,
        args=(user_text, assistant_text, user_id),
        daemon=True,
    ).start()
