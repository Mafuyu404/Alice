"""LLM-based portrait selection worker."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from kokoro.core import config as cfg
from kokoro.core import llm_client
from kokoro.core import prompts
from kokoro.core import token_usage
from kokoro.action.tools.say.portrait_client import PortraitOverlayClient
from kokoro.action.tools.say.portrait_notes import load_portrait_notes

logger = logging.getLogger(__name__)


class PortraitDecisionWorker:
    def __init__(
        self,
        client: PortraitOverlayClient,
        model: str,
        character_id: str = "",
        notes: Optional[list[dict]] = None,
    ):
        self.client = client
        self.model = model
        self.character_id = character_id
        self.notes = notes if notes is not None else load_portrait_notes(character_id)
        self.interval = float(cfg.get("portrait_decision_interval", 2.0))
        self._state_lock = threading.Lock()
        self._user_text = ""
        self._assistant_text = ""
        self._wake_event = threading.Event()
        self._running = True
        self._current_id = ""
        self._notes_by_id = {item["id"]: item.get("notes", "") for item in self.notes}
        self._last_dialogue_time: float = 0.0
        self._pending: bool = False
        self._decay_seconds: float = max(0.0, float(cfg.get("portrait_decay_seconds", 60.0)))
        self._neutral_id: str = self._find_neutral_id()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _find_neutral_id(self) -> str:
        notes = load_portrait_notes(self.character_id)
        for item in notes:
            if "neutral" in item["id"]:
                return item["id"]
        return ""

    def submit(self, user_text: str, assistant_text: str) -> None:
        if not self.notes or not assistant_text:
            return
        with self._state_lock:
            self._user_text = user_text
            self._assistant_text = assistant_text
            self._last_dialogue_time = time.monotonic()
            self._pending = True
        self._wake_event.set()

    def stop(self) -> None:
        self._running = False
        self._wake_event.set()

    def _loop(self) -> None:
        while self._running:
            started_at = time.time()

            # Decay to neutral after prolonged silence
            idle_time = time.monotonic() - self._last_dialogue_time
            if idle_time > self._decay_seconds and self._neutral_id:
                if self._current_id != self._neutral_id:
                    self.client.show(self._neutral_id)
                    self._current_id = self._neutral_id
                    print(f"  [portrait] decay → neutral  (idle={idle_time:.0f}s)")

            # Process pending dialogue
            if self._pending:
                with self._state_lock:
                    user_text = self._user_text
                    assistant_text = self._assistant_text
                    idle_time = time.monotonic() - self._last_dialogue_time
                try:
                    selected = self._decide(user_text, assistant_text, idle_time)
                    if selected:
                        if selected != self._current_id and self.client.show(selected):
                            self._current_id = selected
                            note = self._notes_by_id.get(selected, "")
                            print(f"  [portrait] {selected} ({note})")
                except Exception as exc:
                    print(f"  [portrait] decision failed: {exc}")
                    logger.debug("portrait decision failed: %s", exc)
                self._pending = False

            elapsed = time.time() - started_at
            self._wake_event.wait(max(0.1, self.interval - elapsed))
            self._wake_event.clear()

    def _decide(self, user_text: str, assistant_text: str, idle_time: float = 0.0) -> str:
        valid_ids = {item["id"] for item in self.notes}
        current = self.client.status().get("current") or {}
        current_id = current.get("new_name") or self._current_id or "none"
        if current_id != "none":
            self._current_id = current_id

        # Build time-aware context for the LLM
        time_info = ""
        if idle_time > 10.0:
            time_info = prompts.format_prompt("portrait_selection.time_info_idle", seconds=f"{idle_time:.0f}")
        elif idle_time > 0.0:
            time_info = prompts.get("portrait_selection.time_info_recent", "")

        catalog = "\n".join(f"- {item['id']}: {item.get('notes', '')}" for item in self.notes)

        char_name = self.character_id.capitalize()
        try:
            from kokoro.core import character as _char_mod
            chars = _char_mod.load()
            char_data = chars.get(self.character_id, {})
            if isinstance(char_data, dict):
                char_name = char_data.get("name", char_name)
        except Exception:
            pass
        user_name = cfg.user_name()

        messages = [
            {
                "role": "system",
                "content": prompts.get("portrait_selection.system"),
            },
            {
                "role": "user",
                "content": prompts.format_prompt(
                    "portrait_selection.user_template",
                    current_id=current_id,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    time_info=time_info,
                    catalog=catalog,
                    user_name=user_name,
                    name=char_name,
                ),
            },
        ]
        usage_cb = token_usage.make_callback(self.model, "portrait")
        reply = "".join(llm_client.stream_chat(messages, self.model, timeout=60, usage_callback=usage_cb)).strip()
        for token in reply.replace("`", "").replace("\"", "").split():
            if token in valid_ids:
                return token
        for portrait_id in valid_ids:
            if portrait_id in reply:
                return portrait_id
        return ""
