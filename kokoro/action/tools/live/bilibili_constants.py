"""Bilibili live protocol and API constants."""

from __future__ import annotations

import struct

HEADER_STRUCT = struct.Struct(">I H H I I")
HEARTBEAT_INTERVAL = 30.0
RECV_TIMEOUT = 1.0
BROTLI_AVAILABLE: bool | None = None

OP_HEARTBEAT = 2
OP_HEARTBEAT_REPLY = 3
OP_DATA = 5
OP_AUTH = 7
OP_AUTH_REPLY = 8

PROTO_JSON = 0
PROTO_INT = 1
PROTO_ZLIB = 2
PROTO_BROTLI = 3

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
