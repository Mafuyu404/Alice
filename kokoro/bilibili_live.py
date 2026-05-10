"""Bilibili live room danmaku reader.

Connects to Bilibili live room via WebSocket, receives danmaku,
prints each one to console, and stores them in a buffer for the
impulse planner to consume during live mode.
"""

from __future__ import annotations

import hashlib
import json
import logging
import struct
import threading
import time
import urllib.parse
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Bilibili WebSocket protocol constants
HEADER_STRUCT = struct.Struct(">I H H I I")
HEARTBEAT_INTERVAL = 30.0
RECV_TIMEOUT = 1.0
BROTLI_AVAILABLE: bool | None = None

# Operation codes
OP_HEARTBEAT = 2
OP_HEARTBEAT_REPLY = 3
OP_DATA = 5
OP_AUTH = 7
OP_AUTH_REPLY = 8

# Protocol versions
PROTO_JSON = 0
PROTO_INT = 1
PROTO_ZLIB = 2
PROTO_BROTLI = 3

# REST API
_ROOM_INIT_URL = "https://api.live.bilibili.com/room/v1/Room/room_init?id={}"
_DANMU_INFO_URL = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"
_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
_FINGER_SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi/"
_API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://live.bilibili.com",
}
_WBI_MIXIN_TABLE = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def _get_mixin_key(img_key: str, sub_key: str) -> str:
    return "".join((img_key + sub_key)[i] for i in _WBI_MIXIN_TABLE)[:32]


def _enc_wbi(params: dict, img_key: str, sub_key: str) -> str:
    mixin_key = _get_mixin_key(img_key, sub_key)
    curr_time = int(time.time())
    params["wts"] = curr_time
    parts = []
    for key in sorted(params):
        val = str(params[key])
        for ch in ("!", "'", "(", ")", "*"):
            val = val.replace(ch, "")
        parts.append(f"{key}={urllib.parse.quote(val, safe='')}")
    query = "&".join(parts)
    wbi_sign = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return f"{query}&w_rid={wbi_sign}"


def _get_buvid3() -> str:
    try:
        resp = requests.get(_FINGER_SPI_URL, headers=_API_HEADERS, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data", {}).get("b_3", "")
    except Exception:
        pass
    return ""


def _get_wbi_keys() -> tuple[str, str]:
    resp = requests.get(_NAV_URL, headers=_API_HEADERS, timeout=10)
    data = resp.json()
    wbi_img = data.get("data", {}).get("wbi_img", {})
    img_url = wbi_img.get("img_url", "")
    sub_url = wbi_img.get("sub_url", "")
    img_key = img_url.rsplit("/", 1)[-1].rsplit(".", 1)[0] if img_url else ""
    sub_key = sub_url.rsplit("/", 1)[-1].rsplit(".", 1)[0] if sub_url else ""
    return img_key, sub_key


def _brotli_available() -> bool:
    global BROTLI_AVAILABLE
    if BROTLI_AVAILABLE is None:
        try:
            import brotli  # noqa: F401
            BROTLI_AVAILABLE = True
        except ImportError:
            BROTLI_AVAILABLE = False
    return BROTLI_AVAILABLE


# ═══════════════════════════════════════════════════════════════════════════════
# Protocol helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _encode_packet(body: bytes, operation: int, proto_ver: int = 0) -> bytes:
    header_len = 16
    total_len = header_len + len(body)
    return HEADER_STRUCT.pack(total_len, header_len, proto_ver, operation, 1) + body


def _build_auth_packet(room_id: int, token: str = "", buvid: str = "") -> bytes:
    payload: dict = {
        "uid": 0,
        "roomid": room_id,
        "protover": 3,
        "platform": "web",
        "clientver": "2.0.0",
        "type": 2,
    }
    if token:
        payload["key"] = token
    if buvid:
        payload["buvid"] = buvid
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return _encode_packet(body, OP_AUTH, PROTO_JSON)


def _build_heartbeat_packet() -> bytes:
    return _encode_packet(b"[]", OP_HEARTBEAT, PROTO_JSON)


def _parse_packets(data: bytes) -> list[dict]:
    results: list[dict] = []
    offset = 0
    while offset + 16 <= len(data):
        total_len, header_len, proto_ver, operation, _seq = HEADER_STRUCT.unpack_from(data, offset)
        if total_len < header_len or offset + total_len > len(data):
            break
        body = data[offset + header_len: offset + total_len]

        if proto_ver == PROTO_ZLIB:
            try:
                results.extend(_parse_packets(zlib.decompress(body)))
            except Exception as exc:
                logger.debug("zlib decompress failed: %s", exc)
        elif proto_ver == PROTO_BROTLI:
            if _brotli_available():
                try:
                    import brotli
                    results.extend(_parse_packets(brotli.decompress(body)))
                except Exception as exc:
                    logger.debug("brotli decompress failed: %s", exc)
            else:
                logger.debug("brotli not available, skipping packet")
        elif proto_ver in (PROTO_JSON, PROTO_INT):
            try:
                text = body.decode("utf-8", errors="replace").strip()
                if not text:
                    pass
                elif operation == OP_HEARTBEAT_REPLY and proto_ver == PROTO_INT:
                    results.append({"operation": operation, "body": {"popularity": int(text)}})
                else:
                    results.append({"operation": operation, "body": json.loads(text)})
            except (json.JSONDecodeError, ValueError) as exc:
                logger.debug("packet parse failed: %s", exc)
        else:
            logger.debug("unknown proto_ver %d", proto_ver)
        offset += total_len
    return results


def _parse_danmaku(body: dict) -> Optional[tuple[str, str]]:
    cmd = body.get("cmd", "")
    if not cmd.startswith("DANMU_MSG"):
        return None
    info = body.get("info", [])
    if not isinstance(info, (list, tuple)) or len(info) < 3:
        return None
    text = str(info[1]).strip()
    if not text:
        return None
    user_info = info[2]
    if not isinstance(user_info, (list, tuple)) or len(user_info) < 2:
        user = "观众"
    else:
        user = str(user_info[1]).strip()
        if not user:
            user = "观众"
    return user, text


# ═══════════════════════════════════════════════════════════════════════════════
# DanmakuBuffer
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DanmakuEntry:
    timestamp: float
    user: str
    text: str


class DanmakuBuffer:
    """Thread-safe bounded danmaku buffer with time-based expiry."""

    def __init__(self, max_age: float = 120.0):
        self._max_age = max_age
        self._entries: list[DanmakuEntry] = []
        self._lock = threading.Lock()

    def add(self, user: str, text: str) -> None:
        with self._lock:
            self._entries.append(DanmakuEntry(timestamp=time.time(), user=user, text=text))
            self._trim_locked()

    def drain(self) -> list[DanmakuEntry]:
        with self._lock:
            entries = list(self._entries)
            self._entries.clear()
            return entries

    def peek(self) -> list[DanmakuEntry]:
        with self._lock:
            self._trim_locked()
            return list(self._entries)

    def count(self) -> int:
        with self._lock:
            self._trim_locked()
            return len(self._entries)

    def _trim_locked(self) -> None:
        cutoff = time.time() - self._max_age
        self._entries = [e for e in self._entries if e.timestamp > cutoff]


# ═══════════════════════════════════════════════════════════════════════════════
# BilibiliLiveManager
# ═══════════════════════════════════════════════════════════════════════════════

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

    # ── public API for impulse planner ───────────────────────────────────

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
