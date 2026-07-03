"""Portrait overlay HTTP client and process control."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Optional
from urllib import error, request

from kokoro.action.tools.say.portrait_config import DEFAULT_HOST, DEFAULT_PORT, OVERLAY_SCRIPT, ROOT

logger = logging.getLogger(__name__)


class PortraitOverlayClient:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        character_id: str = "",
        slot_index: int | None = None,
        slot_count: int = 1,
        state_file: str = "",
    ):
        self.host = host
        self.port = port
        self.character_id = character_id
        self.slot_index = slot_index
        self.slot_count = max(1, slot_count)
        self.state_file = state_file
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
        cmd = [
            "python",
            str(OVERLAY_SCRIPT),
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        if self.character_id:
            image_dir = f"characters/{self.character_id}/portrait"
            cmd.extend(["--image-dir", image_dir])
            cmd.extend(["--character-id", self.character_id])
        if self.state_file:
            cmd.extend(["--state-file", self.state_file])
        if self.slot_index is not None:
            cmd.extend(["--slot-index", str(self.slot_index), "--slot-count", str(self.slot_count)])
        self.process = subprocess.Popen(
            cmd,
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
