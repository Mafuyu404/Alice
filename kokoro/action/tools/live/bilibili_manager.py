"""Bilibili live WebSocket manager runtime."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import requests

from kokoro.action.tools.live.bilibili_api import _enc_wbi, _get_buvid3, _get_wbi_keys
from kokoro.action.tools.live.bilibili_buffer import DanmakuBuffer, DanmakuEntry
from kokoro.action.tools.live.bilibili_constants import (
    HEARTBEAT_INTERVAL,
    OP_AUTH_REPLY,
    OP_DATA,
    RECV_TIMEOUT,
    _API_HEADERS,
    _DANMU_INFO_URL,
    _ROOM_INIT_URL,
)
from kokoro.action.tools.live.bilibili_protocol import (
    _build_auth_packet,
    _build_heartbeat_packet,
    _parse_danmaku,
    _parse_packets,
)

logger = logging.getLogger(__name__)


class BilibiliLiveManager:
    """Manages WebSocket connection to Bilibili live and buffers danmaku."""

    def __init__(
        self,
        *,
        room_id: int,
        buffer_max_age: float = 120.0,
        reconnect_delay: float = 5.0,
    ):
        self.room_id = room_id
        self.reconnect_delay = reconnect_delay
        self._buffer = DanmakuBuffer(max_age=buffer_max_age)
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.room_id <= 0:
            logger.warning("bilibili: invalid room_id %d, not starting", self.room_id)
            return
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("bilibili: started for room %d", self.room_id)

    def stop(self) -> None:
        self._cancel.set()

    @property
    def is_connected(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── public API for proactive dialogue ────────────────────────────────

    def drain_danmaku(self) -> list[DanmakuEntry]:
        """Drain and return all buffered danmaku."""
        return self._buffer.drain()

    def get_danmaku_context(self, max_entries: int = 30) -> str:
        """Format recent danmaku as context text for the planner."""
        entries = self._buffer.peek()
        if not entries:
            return ""
        lines = [f"<{e.user}> {e.text}" for e in entries[-max_entries:]]
        return "\n".join(lines)

    def get_user_summaries(self) -> list[tuple[str, str, int]]:
        """Return (user, last_text, count) for each unique user in buffer."""
        entries = self._buffer.peek()
        by_user: dict[str, tuple[str, int]] = {}
        for e in entries:
            if e.user in by_user:
                user_text, cnt = by_user[e.user]
                by_user[e.user] = (e.text, cnt + 1)
            else:
                by_user[e.user] = (e.text, 1)
        return [(u, t, c) for u, (t, c) in by_user.items()]

    # ── internal ─────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._cancel.is_set():
            try:
                self._connect_and_read()
            except requests.ConnectionError as exc:
                print(f"\n  [bilibili] Network error (retry in {self.reconnect_delay:.0f}s): {exc}")
            except TimeoutError:
                print(f"\n  [bilibili] Timeout (retry in {self.reconnect_delay:.0f}s)")
            except Exception as exc:
                print(f"\n  [bilibili] Connection error ({type(exc).__name__}): {exc}")
            if self._cancel.wait(self.reconnect_delay):
                break
            print(f"  [bilibili] Reconnecting to room {self.room_id}...")

    def _get_room_info(self) -> tuple[str, str, str]:
        print(f"\n  [bilibili] Resolving room {self.room_id}...")
        init_resp = requests.get(
            _ROOM_INIT_URL.format(self.room_id),
            headers=_API_HEADERS,
            timeout=10,
        )
        init_data = init_resp.json()
        if init_data.get("code") != 0:
            raise ConnectionError(
                f"room_init failed ({init_data.get('code')}): {init_data.get('msg', '')}"
            )
        real_id = init_data["data"]["room_id"]
        print(f"  [bilibili] Real room ID: {real_id}")

        buvid = _get_buvid3()
        print(f"  [bilibili] buvid3: {buvid[:20] if buvid else 'empty'}...")

        img_key, sub_key = _get_wbi_keys()
        params = {"id": real_id, "type": 0, "web_location": 444.8}
        signed_query = _enc_wbi(params, img_key, sub_key)
        dm_url = f"{_DANMU_INFO_URL}?{signed_query}"

        dm_headers = {
            **_API_HEADERS,
            "Referer": "https://www.bilibili.com/",
            "Cookie": f"buvid3={buvid};",
        }
        dm_resp = requests.get(dm_url, headers=dm_headers, timeout=10)
        dm_data = dm_resp.json()
        if dm_data.get("code") != 0:
            raise ConnectionError(
                f"getDanmuInfo failed ({dm_data.get('code')}): "
                f"{dm_data.get('msg') or dm_data.get('message', '')}"
            )

        hosts = dm_data["data"].get("host_list", [])
        token = dm_data["data"].get("token", "")
        if not hosts:
            raise ConnectionError("no WebSocket hosts returned")

        host_entry = hosts[0]
        host = host_entry["host"]
        wss_port = host_entry.get("wss_port", 443)
        ws_url = f"wss://{host}:{wss_port}/sub"
        print(f"  [bilibili] WS host: {host}:{wss_port}, token: {token[:10] if token else 'empty'}...")
        return ws_url, token, buvid

    def _connect_and_read(self) -> None:
        from websockets.sync.client import connect

        ws_url, token, buvid = self._get_room_info()
        print(f"  [bilibili] Connecting WebSocket...")
        with connect(
            ws_url,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=5,
            user_agent_header="Mozilla/5.0",
        ) as ws:
            ws.send(_build_auth_packet(self.room_id, token=token, buvid=buvid))
            auth_data = ws.recv(timeout=10)
            if not self._check_auth(auth_data):
                raise ConnectionError(f"auth failed for room {self.room_id}")

            print(f"  [bilibili] Connected to room {self.room_id}")
            last_hb = time.monotonic()

            while not self._cancel.is_set():
                now = time.monotonic()
                if now - last_hb >= HEARTBEAT_INTERVAL:
                    try:
                        ws.send(_build_heartbeat_packet())
                        last_hb = now
                    except Exception:
                        break

                try:
                    data = ws.recv(timeout=RECV_TIMEOUT)
                except TimeoutError:
                    continue

                if data is None:
                    break
                if not isinstance(data, bytes):
                    continue

                for pkt in _parse_packets(data):
                    if pkt.get("operation") == OP_DATA:
                        parsed = _parse_danmaku(pkt.get("body", {}))
                        if parsed:
                            user, text = parsed
                            self._buffer.add(user, text)

    def _check_auth(self, raw: bytes) -> bool:
        try:
            for pkt in _parse_packets(raw):
                if pkt.get("operation") == OP_AUTH_REPLY:
                    body = pkt.get("body", {})
                    if isinstance(body, dict) and body.get("code") == 0:
                        return True
        except Exception:
            pass
        return False
