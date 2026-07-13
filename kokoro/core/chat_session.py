"""Shared character chat session with memory and bounded history."""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from kokoro.core import character
from kokoro.core import input_events
from kokoro.core import lifecycle_debug
from kokoro.core import memory as memory_mod
from kokoro.core import prompts
from kokoro.core import scene as scene_mod

logger = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    memory_system: object = field(default=None)  # kokoro.memory.MemorySystem
    input_registry: object = field(default=None)  # InputTypeRegistry
    event_bus: object = field(default=None)  # InputEventBus
    inner_stream_loop: object = field(default=None)  # InnerStreamLoop
    autonomous_step: object = field(default=None)  # AutonomousStep
    life_runtime: object = field(default=None)  # LifeRuntime
    auto_life_runtime: bool = True
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
        lifecycle_debug.log(
            "chat_session.init.start",
            character_id=self.character_id,
            character_name=self.character_data.get("name", ""),
            user_name=self.user_name,
        )
        from kokoro.core.cognition import CognitionStore
        from kokoro.core.emotion import EmotionState
        from kokoro.core.inner_stream import InnerStream, InnerStreamLoop
        from kokoro.core.memory_events import MemoryEventStore
        from kokoro.memory import create_memory_system
        if self.cognition is None:
            self.cognition = CognitionStore(self.character_id, self.character_data)
        if self.emotion is None:
            self.emotion = EmotionState(self.character_id)
        if self.inner_stream is None:
            from kokoro.core import config as _cfg
            section = _cfg.inner_stream_config()
            self.inner_stream = InnerStream(
                self.character_id,
                self.character_data,
                reset_on_start=bool(section.get("reset_on_start", True)),
            )
            lifecycle_debug.log(
                "chat_session.inner_stream.created",
                character_id=self.character_id,
                reset_on_start=bool(section.get("reset_on_start", True)),
            )
        if self.memory_system is None:
            self.memory_system = create_memory_system(
                character_id=self.character_id,
                root=_PROJECT_ROOT,
                vector_backend=self.memory_backend,
            )
        if self.memory_events is None:
            self.memory_events = MemoryEventStore(
                self.memory_backend,
                self.character_id,
                memory_system=self.memory_system,
            )
        if self.input_registry is None:
            self.input_registry = input_events.default_registry()
        if self.event_bus is None:
            self.event_bus = input_events.InputEventBus()
        from kokoro.core import config as _cfg
        life_primary = False
        if self.life_runtime is None and self.auto_life_runtime:
            life_section = _cfg.life_runtime_config()
            if bool(life_section.get("enabled", False)):
                try:
                    from kokoro.life import LifeRuntime
                    self.life_runtime = LifeRuntime(session=self, section=life_section)
                    lifecycle_debug.log("chat_session.life_runtime.created", character_id=self.character_id)
                except Exception as exc:
                    logger.warning("failed to initialize life runtime: %s", exc)
                    lifecycle_debug.log("chat_session.life_runtime.error", character_id=self.character_id, error=str(exc))
        if self.life_runtime is not None:
            runtime_section = getattr(self.life_runtime, "section", None)
            if isinstance(runtime_section, dict):
                life_primary = bool(runtime_section.get("primary", True))
            else:
                life_primary = bool(_cfg.life_runtime_config().get("primary", True))
            submit_life_event = getattr(self.life_runtime, "submit", None)
            if callable(submit_life_event) and not getattr(self.life_runtime, "_chat_session_event_bus_attached", False):
                self.event_bus.subscribe(submit_life_event)
                setattr(self.life_runtime, "_chat_session_event_bus_attached", True)
            attach_action_runtime = getattr(self.life_runtime, "attach_action_runtime", None)
            if callable(attach_action_runtime) and getattr(self.life_runtime, "action_runtime", None) is None:
                try:
                    from kokoro.action.life_runtime import create_life_action_runtime

                    action_runtime = create_life_action_runtime(
                        session=self,
                        section=runtime_section if isinstance(runtime_section, dict) else _cfg.life_runtime_config(),
                        search_section=_cfg.inner_stream_search_config(),
                    )
                    attach_action_runtime(action_runtime)
                    lifecycle_debug.log("chat_session.life_runtime.action_runtime_attached", character_id=self.character_id)
                except Exception as exc:
                    logger.warning("failed to attach life action runtime: %s", exc)
                    lifecycle_debug.log(
                        "chat_session.life_runtime.action_runtime_error",
                        character_id=self.character_id,
                        error=str(exc),
                    )
            start_life_runtime = getattr(self.life_runtime, "start", None)
            if callable(start_life_runtime) and not getattr(self.life_runtime, "_chat_session_started", False):
                start_life_runtime()
                setattr(self.life_runtime, "_chat_session_started", True)
        if self.inner_stream_loop is None and not life_primary:
            search_section = _cfg.inner_stream_search_config()
            output_handlers = []
            cognition_reflector = None
            cognition_section = _cfg.inner_cognition_config()
            if bool(cognition_section.get("enabled", True)):
                try:
                    from kokoro.core.inner_cognition_reflection import InnerCognitionReflection
                    cognition_reflector = InnerCognitionReflection(
                        session=self,
                        section=cognition_section,
                    )
                    lifecycle_debug.log("chat_session.inner_cognition_reflector.created", character_id=self.character_id)
                except Exception as exc:
                    logger.warning("failed to initialize inner cognition reflection: %s", exc)
                    lifecycle_debug.log("chat_session.inner_cognition_reflector.error", character_id=self.character_id, error=str(exc))
            memory_reflector = None
            memory_section = _cfg.inner_memory_config()
            if bool(memory_section.get("enabled", True)):
                try:
                    from kokoro.core.inner_memory_reflection import InnerMemoryReflection
                    memory_reflector = InnerMemoryReflection(
                        session=self,
                        section=memory_section,
                    )
                    lifecycle_debug.log("chat_session.inner_memory_reflector.created", character_id=self.character_id)
                except Exception as exc:
                    logger.warning("failed to initialize inner memory reflection: %s", exc)
                    lifecycle_debug.log("chat_session.inner_memory_reflector.error", character_id=self.character_id, error=str(exc))
            try:
                from kokoro.action.autonomous_step import AutonomousStep
                self.autonomous_step = AutonomousStep(
                    session=self,
                    section=_cfg.autonomous_step_config(),
                    search_section=search_section,
                )
                self.autonomous_step.attach_reflectors(
                    memory_reflector=memory_reflector,
                    cognition_reflector=cognition_reflector,
                )
                life_runtime = getattr(self, "life_runtime", None)
                attach_action_runtime = getattr(life_runtime, "attach_action_runtime", None)
                action_runtime = getattr(self.autonomous_step, "_runtime", None)
                if callable(attach_action_runtime) and action_runtime is not None:
                    attach_action_runtime(action_runtime)
                output_handlers.append(self.autonomous_step.consider_after_inner_stream)
                lifecycle_debug.log("chat_session.autonomous_step.created", character_id=self.character_id)
            except Exception as exc:
                logger.warning("failed to initialize autonomous step: %s", exc)
                lifecycle_debug.log("chat_session.autonomous_step.error", character_id=self.character_id, error=str(exc))
            section = _inner_stream_section()
            self.inner_stream_loop = InnerStreamLoop(
                stream=self.inner_stream,
                context_provider=self._inner_stream_event_context,
                event_delay_seconds=float(section.get("event_wakeup_delay_seconds", section.get("event_merge_seconds", 2.0)) or 2.0),
                idle_interval_seconds=float(section.get("idle_interval_seconds", 240.0) or 240.0),
                time_tick_interval_seconds=float(section.get("time_tick_interval_seconds", 900.0) or 900.0),
                max_batch=int(section.get("event_max_batch", 16) or 16),
                search_impulse=None,
                output_handlers=output_handlers,
            )
            self.event_bus.subscribe(self.inner_stream_loop.submit)
            self.inner_stream_loop.start()
            lifecycle_debug.log(
                "chat_session.inner_stream_loop.created",
                character_id=self.character_id,
                section=section,
            )
        if self._scene is None:
            from kokoro.core import config as _cfg
            self._scene = scene_mod.resolve(_cfg.load())
        lifecycle_debug.log(
            "chat_session.init.done",
            character_id=self.character_id,
            scene=str(self._scene),
        )

    @property
    def character_name(self) -> str:
        return self.character_data["name"]

    _cached_system_prompt: str = ""
    _cached_system_prompt_key: tuple = ()

    @property
    def system_prompt(self) -> str:
        key = (self.character_data.get("name", ""), self.user_name,
               self.character_data.get("system_prompt_template", ""))
        if key != self._cached_system_prompt_key:
            self._cached_system_prompt = character.build_system_prompt(self.character_data, user_name=self.user_name)
            self._cached_system_prompt_key = key
        return self._cached_system_prompt

    @property
    def scene_name(self) -> str:
        """Human-readable scene name for the guidance prefix."""
        return scene_mod.scene_name(self._scene)

    @property
    def scene_guidance(self) -> str:
        """Scene guidance block describing information sources."""
        from kokoro.core import config as _cfg
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

    def _memory_context_for_text(self, text: str) -> str:
        query = str(text or "").strip()
        if not query:
            return ""
        memory_system = getattr(self, "memory_system", None)
        if memory_system is not None:
            try:
                memory_ctx = str(memory_system.deep_recall(query) or "").strip()
                if memory_ctx:
                    return memory_ctx
            except Exception as exc:
                logger.warning("life memory recall failed: %s", exc)
        try:
            return str(
                self.memory_backend.get_context_multi(
                    query,
                    memory_mod.context_user_ids(self.character_id),
                )
                or ""
            ).strip()
        except Exception:
            return ""

    def write_chat_log_to_file(self, filepath: str | None = None) -> str:
        """Write the conversation history to a log file and return the path."""
        if not self.history:
            logger.info("write_chat_log_to_file: no history to write")
            return ""
        if filepath is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            log_dir = str(_PROJECT_ROOT / "logs")
            os.makedirs(log_dir, exist_ok=True)
            filepath = os.path.join(log_dir, f"chat-{self.character_id}-{stamp}.log")
        lines: list[str] = []
        lines.append(f"# Chat Log — {self.character_name} ({self.character_id})")
        lines.append(f"# User: {self.user_name}")
        lines.append(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        if self.summary:
            lines.append(f"# Summary: {self.summary}")
        lines.append("")

        for msg in self.history:
            role = msg.get("role", "")
            content = str(msg.get("content", "") or "").strip()
            if not content:
                continue
            # Skip system messages in the log
            if role in ("system", "tool"):
                continue
            speaker = self.character_name if role == "assistant" else self.user_name
            lines.append(f"{speaker}：{content}")

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            logger.info("chat log written to %s (%d messages)", filepath, len([m for m in self.history if m.get("role") in ("user", "assistant")]))
        except Exception as exc:
            logger.warning("failed to write chat log: %s", exc)
            return ""
        return filepath

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
            memory_ctx = self._memory_context_for_text(user_text)
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

        memory_system = getattr(self, "memory_system", None)
        if memory_system is not None:
            try:
                from kokoro.memory.models import MemoryEventDraft

                memory_system.append_event(
                    MemoryEventDraft(
                        character_id=self.character_id,
                        source="conversation",
                        event_type="conversation_turn",
                        content=(
                            f"{self.user_name}: {user_text}\n"
                            f"{self.character_name}: {assistant_text}"
                        ),
                        memory_policy="experience",
                        participants=[self.user_name, self.character_name],
                        metadata={"path": "full_sedimentation"},
                    )
                )
            except Exception as exc:
                logger.warning("life memory event sedimentation failed: %s", exc)

        # Refresh cognition cache — keyword match against current turn, no LLM
        self.cognition.refresh_cache(user_text, assistant_text)

        # Async emotion evaluation — LLM-based, non-blocking
        threading.Thread(
            target=self.emotion.evaluate,
            args=(user_text, assistant_text, self.character_name, self.user_name),
            daemon=True,
        ).start()

        if str(user_text or "").startswith("【主动对话】"):
            self.record_self_action(
                f"我在没有新的直接发言时主动开口，并说了：{assistant_text}",
                source="speech_output",
                action="speak",
                metadata={"reply": assistant_text, "trigger": user_text},
            )
        else:
            self.record_input_event(
                user_text,
                source="speech",
                metadata={"speaker": self.user_name, "path": "conversation"},
            )
            self.record_self_action(
                f"我刚才选择回应，并说了：{assistant_text}",
                source="speech_output",
                action="speak",
                metadata={"reply": assistant_text},
            )

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
                    memories = self._memory_context_for_text(conv_text[:500])
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

    def _remember_life_memory_async(self, content: str, recent_context: str = "") -> None:
        memory_system = getattr(self, "memory_system", None)
        if memory_system is None:
            return
        try:
            if hasattr(self.inner_stream, "get_text"):
                inner_stream = self.inner_stream.get_text()
            elif hasattr(self.inner_stream, "get_context"):
                inner_stream = self.inner_stream.get_context()
            else:
                inner_stream = ""
        except Exception:
            inner_stream = ""
        try:
            memory_system.remember(
                content,
                inner_stream=inner_stream,
                recent_context=recent_context or self.summary or "",
            )
        except Exception as exc:
            logger.warning("life memory write failed: %s", exc)

    def _eval_cognition_async(self, user_text: str, assistant_text: str) -> None:
        """Periodic lightweight cognition evaluation (no summary needed)."""
        try:
            conv_text = f"{self.user_name}：{user_text}\n{self.character_name}：{assistant_text}"
            memories = self._memory_context_for_text(conv_text[:500])
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
                memories = self._memory_context_for_text(f"{user_text} {assistant_text}"[:500])
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

    def record_input_event(
        self,
        content: str,
        *,
        source: str = "text",
        event_type: str = "text",
        metadata: dict | None = None,
        priority: input_events.InputPriority = "normal",
        lifetime: input_events.InputLifetime = "session",
        privacy: input_events.PrivacyMark | dict | None = None,
    ) -> input_events.InputEvent | None:
        if not str(content or "").strip():
            return None
        try:
            event = self.input_registry.create(
                event_type,
                content,
                source=source,
                metadata=metadata or {},
                privacy=privacy,
                priority=priority,
                lifetime=lifetime,
            )
            lifecycle_debug.log(
                "chat_session.record_input_event",
                character_id=self.character_id,
                event=event,
            )
            memory_system = getattr(self, "memory_system", None)
            if memory_system is not None:
                try:
                    append_input_event = getattr(memory_system, "append_input_event", None)
                    if callable(append_input_event):
                        append_input_event(event)
                except Exception as exc:
                    logger.warning("failed to append life memory event: %s", exc)
            autonomous = getattr(self, "autonomous_step", None)
            note_external = getattr(autonomous, "note_external_event", None)
            if callable(note_external):
                note_external(event)
            self.event_bus.publish(event)
            return event
        except Exception as exc:
            logger.warning("failed to publish input event: %s", exc)
            return None

    def record_self_action(
        self,
        content: str,
        *,
        source: str = "self",
        action: str = "",
        metadata: dict | None = None,
        lifetime: input_events.InputLifetime = "session",
    ) -> input_events.InputEvent | None:
        if not str(content or "").strip():
            return None
        try:
            event = input_events.build_self_action_event(
                content,
                source=source,
                action=action,
                metadata=metadata or {},
                lifetime=lifetime,
            )
            lifecycle_debug.log(
                "chat_session.record_self_action",
                character_id=self.character_id,
                event=event,
            )
            memory_system = getattr(self, "memory_system", None)
            if memory_system is not None:
                try:
                    append_input_event = getattr(memory_system, "append_input_event", None)
                    if callable(append_input_event):
                        append_input_event(event)
                except Exception as exc:
                    logger.warning("failed to append life memory event: %s", exc)
            autonomous = getattr(self, "autonomous_step", None)
            note_external = getattr(autonomous, "note_external_event", None)
            if callable(note_external):
                note_external(event)
            self.event_bus.publish(event)
            return event
        except Exception as exc:
            logger.warning("failed to publish self action event: %s", exc)
            return None

    def _record_inner_stream_search_event(
        self,
        content: str,
        source: str,
        metadata: dict | None = None,
    ) -> None:
        action = str((metadata or {}).get("action") or "web_search")
        query = str((metadata or {}).get("query") or "").strip()
        reason = str((metadata or {}).get("reason") or "").strip()
        if action == "web_search_intent":
            print(f"\n[web_search] intent query={query!r} reason={reason or 'inner stream impulse'}")
        elif action == "web_search_result":
            print(f"\n[web_search] result query={query!r}")
        elif action == "web_search_error":
            error = str((metadata or {}).get("error") or "").strip()
            print(f"\n[web_search] error query={query!r} {error}")
        if action == "web_search_intent":
            self.record_self_action(
                content,
                source=source,
                action=action,
                metadata=metadata or {},
            )
            return
        self.record_input_event(
            content,
            source=source,
            event_type="web_search",
            metadata=metadata or {},
            priority="normal",
            lifetime="session",
        )

    def record_dialogue_observation(self, text: str, *, action: str = "silence", reason: str = "") -> None:
        self.record_input_event(
            text,
            source="speech",
            metadata={"speaker": self.user_name, "path": "dialogue_observation", "decision": action},
            priority="normal",
        )
        content = reason or "我感知到这段话，但判断它更像背景、自言自语或暂时不需要打断当前活动，所以没有立刻回应。"
        self.record_self_action(
            content,
            source="dialogue_orchestrator",
            action=action,
            metadata={"decision": action},
        )

    def flush_inner_stream_events(self) -> None:
        loop = getattr(self, "inner_stream_loop", None)
        if loop is not None and hasattr(loop, "flush"):
            loop.flush()

    def _inner_stream_event_context(self) -> dict:
        lifecycle_debug.log("chat_session.inner_stream_context.start", character_id=self.character_id)
        recent = _format_recent_history(
            self.history[-10:],
            user_name=self.user_name,
            character_name=self.character_name,
        )
        events = []
        bus = getattr(self, "event_bus", None)
        if bus is not None and hasattr(bus, "snapshot"):
            try:
                events = bus.snapshot(20)
            except Exception:
                events = []
        event_text = input_events.format_events_for_prompt(events, max_chars=2500) if events else ""
        try:
            query = " ".join(
                msg.get("content", "")
                for msg in self.history[-4:]
                if isinstance(msg, dict)
            )
            if event_text:
                query = f"{query}\n{event_text}"
            query = query[:1000]
            memories = self._memory_context_for_text(query) if query else ""
        except Exception:
            memories = ""
        cognition_context = ""
        try:
            if self.cognition:
                cognition_context = self.cognition.get_context_for_text(event_text) if event_text else self.cognition.get_context()
        except Exception:
            cognition_context = self.cognition.get_context() if self.cognition else ""
        activity_context = ""
        autonomous = getattr(self, "autonomous_step", None)
        activity_provider = getattr(autonomous, "activity_context", None)
        if callable(activity_provider):
            try:
                activity_context = activity_provider()
            except Exception:
                activity_context = ""
        result = {
            "character_name": self.character_name,
            "user_name": self.user_name,
            "summary": self.summary or "",
            "recent_history": recent,
            "cognition_context": cognition_context,
            "emotion_context": self.emotion.get_context() if self.emotion else "",
            "memory_context": memories or "",
            "scene_context": self.scene_guidance or "",
            "activity_context": activity_context,
        }
        lifecycle_debug.log(
            "chat_session.inner_stream_context.done",
            character_id=self.character_id,
            event_snapshot_count=len(events),
            query=query if "query" in locals() else "",
            context=result,
        )
        return result

    def _call_summary_llm(self, existing_summary: str, conversation: str) -> str | None:
        prompt = prompts.format_prompt(
            "conversation_summary.user_template",
            existing_summary=existing_summary or "无",
            conversation=conversation,
            user_name=self.user_name,
        )
        system = prompts.get("conversation_summary.system", "")

        from kokoro.core import config as cfg
        from kokoro.core import deepseek_api

        model = cfg.stt_refine_model()
        try:
            result = deepseek_api.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                model=model,
                temperature=0.1,
                max_tokens=512,
                function="conversation_summary",
            )
            return result["content"] if result["content"] else None
        except Exception as exc:
            logger.warning("summary LLM call failed: %s", exc)
            return None


def load_session(
    character_id: str,
    memory_backend: object,
    max_history: int = 20,
    cognition_eval_interval: int | None = None,
) -> ChatSession:
    from kokoro.core import config as cfg
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


def _inner_stream_section() -> dict:
    from kokoro.core import config as cfg

    section = cfg.inner_stream_config()
    return section if isinstance(section, dict) else {}
