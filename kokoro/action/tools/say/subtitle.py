"""Subtitle overlay process control — start, stop, push text, clear."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional
from urllib import error, request

from kokoro.core import config as cfg

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[4]
OVERLAY_SCRIPT = ROOT / "overlay_subtitle.py"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17353


class SubtitleOverlayClient:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.process: Optional[subprocess.Popen] = None
        self.owned_process = False
        self._lock = threading.Lock()

    def start(self, config_prefix: str = "subtitle") -> None:
        """Launch the overlay subprocess.

        Args:
            config_prefix: Config section prefix, e.g. "subtitle" or "subtitle_stt".
        """
        # Kill leftover process from a previous run
        if self.is_running():
            print(f"  [{config_prefix}] Shutting down previous overlay instance...")
            self._post("/control", {"action": "shutdown"})
            time.sleep(0.5)

        if not OVERLAY_SCRIPT.exists():
            print(f"  [{config_prefix}] Script not found: {OVERLAY_SCRIPT}")
            return
        print(f"  [{config_prefix}] Launching overlay...")
        cfg_section = cfg.get(config_prefix, {})
        font_color = str(cfg_section.get("font_color", "#8b0000"))
        stroke_color = str(cfg_section.get("stroke_color", "#ffffff"))
        font_size = int(cfg_section.get("font_size", 24))
        btn_color = str(cfg_section.get("btn_color", font_color))
        cmd = [
            "python",
            str(OVERLAY_SCRIPT),
            "--host", self.host,
            "--port", str(self.port),
            "--font-color", font_color,
            "--stroke-color", stroke_color,
            "--font-size", str(font_size),
            "--btn-color", btn_color,
            "--state-file", f"{config_prefix}_state.json",
        ]
        print(f"  [{config_prefix}] Cmd: {' '.join(cmd)}")
        self.process = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.owned_process = True
        if self.wait_until_ready(timeout=10):
            status = self._get("/status")
            pos_info = f"visible={status.get('visible')}" if status else "?"
            print(f"  [{config_prefix}] Overlay ready at {self.base_url} ({pos_info})")
        else:
            print(f"  [{config_prefix}] Overlay did not become ready (process running: {self.process.poll() is None})")
            if self.process.poll() is not None:
                print(f"  [{config_prefix}] Process exited with code {self.process.returncode}")

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

    def push_text(self, text: str, mode: str = "append") -> bool:
        """Send text to the subtitle overlay.

        Args:
            text: Text content.
            mode: "set" replaces all text, "append" adds to existing.
        """
        return self._post("/control", {"action": "text", "text": text, "mode": mode})

    def clear(self) -> bool:
        """Clear all text from the subtitle."""
        return self._post("/control", {"action": "clear"})

    def show(self) -> bool:
        return self._post("/control", {"action": "show"})

    def hide(self) -> bool:
        return self._post("/control", {"action": "hide"})

    def expand(self) -> bool:
        return self._post("/control", {"action": "expand"})

    def collapse(self) -> bool:
        return self._post("/control", {"action": "collapse"})

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

    def _post(self, path: str, payload: dict) -> bool:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=2) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return bool(result.get("ok"))
        except error.HTTPError as exc:
            try:
                result = json.loads(exc.read().decode("utf-8"))
                return bool(result.get("ok"))
            except Exception:
                return False
        except Exception:
            return False


# Global instance for easy access from cli.py
_subtitle_client: Optional[SubtitleOverlayClient] = None


def create_client(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> SubtitleOverlayClient:
    global _subtitle_client
    _subtitle_client = SubtitleOverlayClient(host=host, port=port)
    return _subtitle_client


def create_default_clients(enabled: bool, config: dict) -> tuple[SubtitleOverlayClient | None, SubtitleOverlayClient | None]:
    if not enabled:
        return None, None

    subtitle_cfg = config.get("subtitle", {})
    if not isinstance(subtitle_cfg, dict):
        subtitle_cfg = {}
    subtitle_host = str(subtitle_cfg.get("subtitle_host", DEFAULT_HOST))
    subtitle_port = int(subtitle_cfg.get("subtitle_port", DEFAULT_PORT))
    subtitle_client = SubtitleOverlayClient(host=subtitle_host, port=subtitle_port)
    subtitle_client.start()

    stt_cfg = config.get("subtitle_stt", {})
    if not isinstance(stt_cfg, dict):
        stt_cfg = {}
    stt_host = str(stt_cfg.get("subtitle_host", DEFAULT_HOST))
    stt_port = int(stt_cfg.get("subtitle_port", DEFAULT_PORT + 1))
    stt_subtitle_client = SubtitleOverlayClient(host=stt_host, port=stt_port)
    stt_subtitle_client.start(config_prefix="subtitle_stt")
    return subtitle_client, stt_subtitle_client


def get_client() -> Optional[SubtitleOverlayClient]:
    return _subtitle_client
