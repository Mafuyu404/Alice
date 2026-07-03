"""Bilibili live REST and WBI helpers."""

from __future__ import annotations

import hashlib
import time
import urllib.parse

import requests

from kokoro.action.tools.live.bilibili_constants import (
    _API_HEADERS,
    _FINGER_SPI_URL,
    _NAV_URL,
    _WBI_MIXIN_TABLE,
)


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
