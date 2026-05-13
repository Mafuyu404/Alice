"""Multi-character chat orchestrator.

Supports N AI characters talking to each other and the user in a shared
conversation. Each character has its own ChatSession (memory, cognition,
emotion) and model config. The orchestrator manages turn order and
shared history with speaker labels.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from kokoro import agent_loop
from kokoro import chat_session
from kokoro import config as _cfg
from kokoro import memory as _mem
from kokoro import prompts as _prompts
from kokoro import scene as _scene
from kokoro import token_usage

logger = logging.getLogger(__name__)


# ── Data ────────────────────────────────────────────────────────────────────


@dataclass
class HistoryEntry:
    speaker: str      # character name or user_name
    text: str
    character_id: str = ""  # "" means it's a user message


@dataclass
class MultiChatConfig:
    character_ids: list[str] = field(default_factory=lambda: ["penglai", "alice"])
    max_history: int = 30
    model: str = ""
    enable_tools: bool = False
    # Optional TTS engines keyed by character_id
    tts_engines: dict[str, object] = field(default_factory=dict)
    # Optional portrait worker for expression switching
    portrait_worker: object = None


# ── Orchestrator ────────────────────────────────────────────────────────────


class MultiChatOrchestrator:
    """Orchestrate conversation among multiple AI characters + optional user."""

    def __init__(self, config: MultiChatConfig | None = None):
        self.config = config or MultiChatConfig()
        self.shared_history: list[HistoryEntry] = []

        runtime_cfg = _cfg.load()

        # Resolve scene to multi_chat
        self.scene = _scene.SceneType.MULTI_CHAT
        live_section = runtime_cfg.get("bilibili_live", {})
        if isinstance(live_section, dict) and live_section.get("live_mode", False):
            self.scene = _scene.SceneType.MULTI_LIVE

        user_name = _cfg.user_name()
        memory_backend = _mem.create_backend(runtime_cfg)

        self.user_name = user_name
        self.memory_backend = memory_backend
        self.sessions: dict[str, chat_session.ChatSession] = {}
        self.order: list[str] = []  # character_ids in turn order

        for cid in self.config.character_ids:
            session = chat_session.load_session(
                cid, memory_backend, max_history=self.config.max_history,
            )
            # Override scene
            session._scene = self.scene
            self.sessions[cid] = session
            self.order.append(cid)

        self._turn_index = 0

    @property
    def character_names(self) -> dict[str, str]:
        return {cid: s.character_name for cid, s in self.sessions.items()}

    # ── history ─────────────────────────────────────────────────────────────

    def add_user_message(self, text: str) -> None:
        self.shared_history.append(
            HistoryEntry(speaker=self.user_name, text=text, character_id=""),
        )

    def add_ai_message(self, character_id: str, text: str) -> None:
        name = self.sessions[character_id].character_name
        self.shared_history.append(
            HistoryEntry(speaker=name, text=text, character_id=character_id),
        )

    def _format_history(self, max_entries: int = 20) -> str:
        """Format recent shared history with speaker labels."""
        entries = self.shared_history[-max_entries:] if max_entries else self.shared_history
        return "\n".join(f"{e.speaker}：{e.text}" for e in entries)

    # ── message building ────────────────────────────────────────────────────

    def build_messages_for(
        self,
        character_id: str,
        input_text: str,
        input_speaker: str = "",
    ) -> list[dict]:
        """Build the LLM message array for a character's turn.

        Everything goes into a single user message to avoid role confusion
        in multi-character chat. The system prompt sets the character identity.
        """
        session = self.sessions[character_id]
        my_name = session.character_name

        # -- assemble context blocks --
        parts: list[str] = []

        # Scene guidance
        guidance = _scene.guidance_text(
            self.scene, self.user_name, my_name,
        )
        if guidance:
            prefix = _prompts.get("scene.prefix", "【当前场景：{scene_name}】")
            scene_label = prefix.format(scene_name=_scene.scene_name(self.scene))
            parts.append(f"{scene_label}\n{guidance}")

        # Summary
        with session._summarize_lock:
            if session.summary:
                parts.append(f"【对话摘要】\n{session.summary}")

        # Recent conversation history with speaker labels
        history_text = self._format_history(max_entries=self.config.max_history)
        if history_text:
            parts.append(f"【最近对话】\n{history_text}")

        # Memory context
        memory_ctx = session.memory_backend.get_context(
            input_text, user_id=character_id,
        )
        if memory_ctx:
            parts.append(memory_ctx)

        # Cognition layer
        cognition_ctx = session.cognition.get_context()
        if cognition_ctx:
            parts.append(cognition_ctx)

        # Emotion layer
        emotion_ctx = session.emotion.get_context()
        if emotion_ctx:
            parts.append(emotion_ctx)

        # -- build messages --
        messages: list[dict] = [
            {"role": "system", "content": session.system_prompt},
        ]

        if parts:
            messages.append({"role": "system", "content": "\n\n".join(parts)})

        if input_speaker:
            messages.append({
                "role": "user",
                "content": _prompts.format_prompt(
                    "multi_chat.speaker_turn",
                    my_name=my_name,
                    input_speaker=input_speaker,
                    input_text=input_text,
                ),
            })
        elif input_text:
            messages.append({"role": "user", "content": input_text})
        else:
            # Starter: no input yet
            messages.append({
                "role": "user",
                "content": _prompts.format_prompt("multi_chat.starter", my_name=my_name),
            })

        return messages

    # ── turn execution ──────────────────────────────────────────────────────

    def auto_turn(self) -> tuple[str, str, str]:
        """Let the next AI character in the order respond to the latest message.

        Returns:
            (character_id, character_name, reply_text)
        """
        if not self.shared_history:
            # No conversation yet — pick a starter
            return self._starter_turn()

        latest = self.shared_history[-1]
        # Who should speak next? The next character in order after the last speaker.
        next_id = self._next_speaker(latest.character_id)
        session = self.sessions[next_id]

        # What they respond to
        input_text = latest.text
        input_speaker = latest.speaker

        model = self.config.model or session.character_config.get(
            "llm_model", "",
        ) or _cfg.llm_model()

        messages = self.build_messages_for(next_id, input_text, input_speaker)
        result = agent_loop.agent_chat(
            messages, model,
            agent_config=None,
            cancel_event=threading.Event(),
            usage_callback=token_usage.make_callback(model, "multi_chat"),
            capture=True,
        )

        reply = result.reply.strip()
        if reply:
            self.add_ai_message(next_id, reply)
            session.remember(input_text, reply, async_store=True)

        return next_id, session.character_name, reply

    def user_turn(self, user_text: str) -> tuple[str, str, str]:
        """Process user input: store it, then let the next AI respond.

        Returns:
            (character_id, character_name, reply_text)
        """
        self.add_user_message(user_text)
        return self.auto_turn()

    def _starter_turn(self) -> tuple[str, str, str]:
        """No conversation yet — kick off with a greeting from the first character."""
        cid = self.order[0]
        session = self.sessions[cid]
        model = self.config.model or session.character_config.get(
            "llm_model", "",
        ) or _cfg.llm_model()

        msgs = self.build_messages_for(cid, "", "")
        result = agent_loop.agent_chat(
            msgs, model,
            agent_config=None,
            cancel_event=threading.Event(),
            usage_callback=token_usage.make_callback(model, "multi_chat"),
            capture=True,
        )
        reply = result.reply.strip()
        if reply:
            self.add_ai_message(cid, reply)
        return cid, session.character_name, reply

    def _next_speaker(self, last_speaker_id: str) -> str:
        """Determine the next speaker given who spoke last.

        If last_speaker_id is a character, pick the *other* character.
        If last_speaker_id is the user (""), pick the first in the order.
        """
        if not last_speaker_id:
            return self.order[0]
        idx = self.order.index(last_speaker_id) if last_speaker_id in self.order else -1
        return self.order[(idx + 1) % len(self.order)]

    # ── auto-run ────────────────────────────────────────────────────────────

    def auto_cycle(self, rounds: int = 5, init_prompt: str = "") -> list[tuple[str, str, str]]:
        """Run N auto turns. Returns list of (cid, name, reply)."""
        turns: list[tuple[str, str, str]] = []

        if init_prompt and not self.shared_history:
            # Use init_prompt as the first "user" message to start the conversation
            self.add_user_message(init_prompt)
            cid, name, reply = self.auto_turn()
            turns.append((cid, name, reply))

        for _ in range(rounds):
            cid, name, reply = self.auto_turn()
            if not reply:
                break
            turns.append((cid, name, reply))

        return turns
