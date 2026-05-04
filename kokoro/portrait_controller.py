"""Portrait overlay process control and LLM-based selection."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional
from urllib import error, request

from kokoro import config as cfg
from kokoro import llm_client
from kokoro import prompts

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
NOTES_PATH = ROOT / "portrait_notes.json"
OVERLAY_SCRIPT = ROOT / "overlay_slideshow.py"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17352


def load_portrait_notes(path: Path = NOTES_PATH) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("portraits", [])


class PortraitOverlayClient:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.process: Optional[subprocess.Popen] = None
        self.owned_process = False

    def start(self) -> None:
        if self.is_running():
            print("  [portrait] Connected to existing overlay")
            return
        if not OVERLAY_SCRIPT.exists():
            logger.warning("overlay script not found: %s", OVERLAY_SCRIPT)
            return
        print("  [portrait] Starting overlay...")
        self.process = subprocess.Popen(
            [
                "python",
                str(OVERLAY_SCRIPT),
                "--host",
                self.host,
                "--port",
                str(self.port),
            ],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.owned_process = True
        if self.wait_until_ready(timeout=8):
            print(f"  [portrait] Overlay ready: {self.base_url}")
        else:
            print(f"  [portrait] Overlay did not become ready: {self.base_url}")
        self.pause()

    def is_running(self) -> bool:
        try:
            with request.urlopen(f"{self.base_url}/health", timeout=0.5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def wait_until_ready(self, timeout: float = 8.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_running():
                return True
            time.sleep(0.2)
        return False

    def status(self) -> dict:
        return self._get("/status") or {}

    def show(self, name: str) -> bool:
        result = self._post("/control", {"action": "show", "name": name})
        ok = bool(result and result.get("ok"))
        if not ok:
            print(f"  [portrait] show failed -> {name}: {result}")
        return ok

    def send_debug(self, data: dict) -> None:
        self._post("/debug", {"data": data})

    def pause(self) -> None:
        self._post("/control", {"action": "pause"})

    def shutdown(self) -> None:
        if not self.owned_process:
            return
        self._post("/control", {"action": "shutdown"})
        if self.process and self.process.poll() is None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
        self.process = None
        self.owned_process = False

    def _get(self, path: str) -> Optional[dict]:
        try:
            with request.urlopen(f"{self.base_url}{path}", timeout=2) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def _post(self, path: str, payload: dict) -> Optional[dict]:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=2) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8"))
            except Exception:
                return None
        except Exception:
            return None


class PortraitDecisionWorker:
    def __init__(
        self,
        client: PortraitOverlayClient,
        model: str,
        notes: Optional[list[dict]] = None,
    ):
        self.client = client
        self.model = model
        self.notes = notes if notes is not None else load_portrait_notes()
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

    @staticmethod
    def _find_neutral_id() -> str:
        notes = load_portrait_notes()
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
                ),
            },
        ]
        reply = "".join(llm_client.stream_chat(messages, self.model, timeout=60)).strip()
        for token in reply.replace("`", "").replace("\"", "").split():
            if token in valid_ids:
                return token
        for portrait_id in valid_ids:
            if portrait_id in reply:
                return portrait_id
        return ""


def create_controller(model: str) -> tuple[PortraitOverlayClient, PortraitDecisionWorker]:
    host = cfg.get("portrait_overlay_host", DEFAULT_HOST)
    port = int(cfg.get("portrait_overlay_port", DEFAULT_PORT))
    client = PortraitOverlayClient(host=host, port=port)
    client.start()
    worker = PortraitDecisionWorker(client=client, model=model)
    return client, worker
