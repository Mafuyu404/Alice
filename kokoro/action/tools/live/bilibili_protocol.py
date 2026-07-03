"""Bilibili live WebSocket protocol helpers."""

from __future__ import annotations

import json
import logging
import zlib
from typing import Optional

from kokoro.action.tools.live import bilibili_constants as constants
from kokoro.action.tools.live.bilibili_constants import (
    HEADER_STRUCT,
    OP_AUTH,
    OP_AUTH_REPLY,
    OP_DATA,
    OP_HEARTBEAT,
    OP_HEARTBEAT_REPLY,
    PROTO_BROTLI,
    PROTO_INT,
    PROTO_JSON,
    PROTO_ZLIB,
)

logger = logging.getLogger(__name__)


def _brotli_available() -> bool:
    if constants.BROTLI_AVAILABLE is None:
        try:
            import brotli  # noqa: F401
            constants.BROTLI_AVAILABLE = True
        except ImportError:
            constants.BROTLI_AVAILABLE = False
    return bool(constants.BROTLI_AVAILABLE)


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
