"""Central runtime configuration.

Primary config file: config.toml.
Local secrets override: config.json (gitignored, for API keys and local-only values).
TOML values take precedence; JSON fills in empty/missing entries.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_TOML_PATH = _ROOT / "config.toml"
_CONFIG_JSON_PATH = _ROOT / "config.json"
_CONFIG: dict | None = None


def _clean_proxy() -> None:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"


def _load_toml() -> dict:
    if not _CONFIG_TOML_PATH.exists():
        return {}
    with _CONFIG_TOML_PATH.open("rb") as file:
        return tomllib.load(file)


def _load_json() -> dict:
    if not _CONFIG_JSON_PATH.exists():
        return {}
    with _CONFIG_JSON_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def _merge_fallback(primary: dict, fallback: dict) -> dict:
    result = dict(primary)
    for key, value in fallback.items():
        if key not in result or result[key] in ("", None, [], {}):
            result[key] = value
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_fallback(result[key], value)
    return result


def load() -> dict:
    global _CONFIG
    if _CONFIG is None:
        _clean_proxy()
        toml_config = _load_toml()
        json_config = _load_json()
        _CONFIG = _merge_fallback(toml_config, json_config)
    return _CONFIG


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def llm_url() -> str:
    return get("llm_url", "http://127.0.0.1:11434")


def llm_model() -> str:
    return get("llm_model", "deepseek-v4-flash")


def memory_backend() -> str:
    return get("memory_backend", "mem0")


def cartesia_api_key() -> str:
    return get("cartesia_api_key", "")


def tts_voice_id() -> str:
    return get("tts_voice_id", "")


def tts_sample_rate() -> int:
    return int(get("tts_sample_rate", 24000))


def tts_backend() -> str:
    return get("tts_backend", "minimax")


def minimax_api_key() -> str:
    return get("minimax_api_key", "")


def minimax_model() -> str:
    return get("minimax_model", "speech-2.8-turbo")


def kokoromo_url() -> str:
    return get("kokoromo_url", "")


def deepseek_api_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY") or get("deepseek_api_key", "")


def deepseek_url() -> str:
    return "https://api.deepseek.com"


def is_deepseek_model(model: str) -> bool:
    return model.startswith("deepseek")


def stt_refine_model() -> str:
    return get("stt_refine_model", "qwen2.5:1.5b")


def stt_refine_mode() -> str:
    return get("stt_refine_mode", "separate")


def stt_pause_during_tts() -> bool:
    return bool(get("stt_pause_during_tts", False))


def stt_refine_stable_seconds() -> float:
    return float(get("stt_refine_stable_seconds", 1.5))


def stt_pool_tick_seconds() -> float:
    return float(get("stt_pool_tick_seconds", 0.05))


def stt_refine_max_tokens() -> int:
    return int(get("stt_refine_max_tokens", 128))


def stt_skip_short_refine() -> bool:
    return bool(get("stt_skip_short_refine", True))


def stt_skip_short_refine_max_chars() -> int:
    return int(get("stt_skip_short_refine_max_chars", 18))


def api_base() -> str:
    kb = kokoromo_url()
    mb = memory_backend()
    lu = llm_url()
    if mb in ("mem0", "none") or not kb:
        return lu + "/v1"
    return kb + "/v1"
