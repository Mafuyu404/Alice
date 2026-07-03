"""Central runtime configuration.

Primary config files: config/*.toml, merged by filename order.
Legacy fallback: config.toml.
Local secrets override: config.json (gitignored, for API keys and local-only values).
TOML values take precedence; JSON fills in empty/missing entries.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _ROOT / "config"
_CONFIG_TOML_PATH = _ROOT / "config.toml"
_CONFIG_JSON_PATH = _ROOT / "config.json"


def _clean_proxy() -> None:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"


def _load_toml_file(path: Path) -> dict:
    raw = path.read_bytes()
    # Strip UTF-8 BOM if present (Python 3.12 tomllib doesn't handle it)
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    return tomllib.loads(raw.decode("utf-8"))


def _merge_override(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge_override(result[key], value)
        else:
            result[key] = value
    return result


def _load_toml() -> dict:
    if _CONFIG_DIR.is_dir():
        config: dict = {}
        paths = sorted(path for path in _CONFIG_DIR.glob("*.toml") if path.is_file())
        if paths:
            for path in paths:
                config = _merge_override(config, _load_toml_file(path))
            return config
    if not _CONFIG_TOML_PATH.exists():
        return {}
    return _load_toml_file(_CONFIG_TOML_PATH)


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
    _clean_proxy()
    toml_config = _load_toml()
    json_config = _load_json()
    return _merge_fallback(toml_config, json_config)


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def section_get(section: str, key: str, default: Any = None) -> Any:
    data = load()
    section_data = data.get(section, {})
    if isinstance(section_data, dict) and key in section_data:
        return section_data[key]
    if key in data:
        return data[key]
    # Compatibility for older config.toml files where STT keys appeared after
    # [aec] without a new table header.
    if section == "stt":
        aec_data = data.get("aec", {})
        if isinstance(aec_data, dict) and key in aec_data:
            return aec_data[key]
    return default


def llm_url() -> str:
    return get("llm_url", "http://127.0.0.1:11434")


def llm_model() -> str:
    return get("llm_model", "deepseek-v4-flash")


def dialogue_model() -> str:
    return get("dialogue_model", llm_model())


def memory_backend() -> str:
    return get("memory_backend", "mem0")


def cartesia_api_key() -> str:
    return get("cartesia_api_key", "")


def tts_voice_id() -> str:
    return get("tts_voice_id", "")


def tts_sample_rate() -> int:
    return int(get("tts_sample_rate", 24000))


def tts_volume() -> float:
    return max(0.0, min(2.0, float(get("tts_volume", 1.0))))


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


def charglm_api_key() -> str:
    return get("charglm_api_key", "")


def deepseek_url() -> str:
    return "https://api.deepseek.com"


def is_deepseek_model(model: str) -> bool:
    return model.startswith("deepseek")


def user_name() -> str:
    """当前与 AI 对话的用户名称。用于提示词和认知/情绪/记忆层。"""
    return get("user_name", "真冬")


def stt_refine_model() -> str:
    return section_get("stt", "stt_refine_model", "qwen2.5:1.5b")


def stt_enabled() -> bool:
    return bool(section_get("stt", "enabled", True))


def stt_refine_mode() -> str:
    return section_get("stt", "stt_refine_mode", "separate")


def stt_pause_during_tts() -> bool:
    return bool(section_get("stt", "stt_pause_during_tts", False))


def stt_echo_filter_seconds() -> float:
    return float(section_get("stt", "stt_echo_filter_seconds", 45.0))


def stt_echo_filter_min_chars() -> int:
    return int(section_get("stt", "stt_echo_filter_min_chars", 4))


def stt_echo_filter_similarity() -> float:
    return float(section_get("stt", "stt_echo_filter_similarity", 0.72))


def stt_refine_stable_seconds() -> float:
    return float(section_get("stt", "stt_refine_stable_seconds", 1.5))


def stt_utterance_commit_seconds() -> float:
    return float(section_get("stt", "stt_utterance_commit_seconds", 0.55))


def stt_short_utterance_extra_seconds() -> float:
    return float(section_get("stt", "stt_short_utterance_extra_seconds", 1.4))


def stt_short_utterance_max_chars() -> int:
    return int(section_get("stt", "stt_short_utterance_max_chars", 8))


def stt_turn_merge_seconds() -> float:
    return float(section_get("stt", "stt_turn_merge_seconds", 1.4))


def stt_dialogue_pool_enabled() -> bool:
    return bool(section_get("stt", "stt_dialogue_pool_enabled", True))


def stt_pool_tick_seconds() -> float:
    return float(section_get("stt", "stt_pool_tick_seconds", 0.05))


def stt_refine_max_tokens() -> int:
    return int(section_get("stt", "stt_refine_max_tokens", 128))


def stt_skip_short_refine() -> bool:
    return bool(section_get("stt", "stt_skip_short_refine", True))


def stt_skip_short_refine_max_chars() -> int:
    return int(get("stt_skip_short_refine_max_chars", 18))


# ── Overlap classifier ────────────────────────────────────────────────────────

def overlap_model() -> str:
    """重叠分类用模型。小模型（0.5B）即可，负责判断用户插话时的打断级别。"""
    return get("overlap_model", "qwen2.5:0.5b")


# ── AEC (Acoustic Echo Cancellation) ──────────────────────────────────────────

def aec_enabled() -> bool:
    section = get("aec", {})
    if not isinstance(section, dict):
        return False
    return bool(section.get("enabled", False))


def aec_delay_ms() -> int:
    section = get("aec", {})
    if not isinstance(section, dict):
        return 50
    return int(section.get("delay_ms", 50))


def aec_ns_level() -> int:
    section = get("aec", {})
    if not isinstance(section, dict):
        return 2
    return int(section.get("ns_level", 2))


def aec_auto_reset_on_tts_done() -> bool:
    section = get("aec", {})
    if not isinstance(section, dict):
        return True
    return bool(section.get("auto_reset_on_tts_done", True))


# ── cognition / emotion ─────────────────────────────────────────────────────────

def cognition_model() -> str:
    """认知层评估用模型。留空则使用 stt_refine_model。"""
    return get("cognition_model", "")


def cognition_eval_interval() -> int:
    """认知层评估频率。每 N 轮对话评估一次。0 = 禁用。"""
    return int(get("cognition_eval_interval", 5))


def emotion_model() -> str:
    """情绪层评估用模型。留空则使用 stt_refine_model。"""
    return get("emotion_model", "")


def portrait_model() -> str:
    """Model for portrait expression selection. Empty = use conversation model."""
    return get("portrait_model", "")


def vision_max_pixels() -> int:
    """Max pixel count for screenshot scaling. 0 = no scaling."""
    return int(get("vision_max_pixels", 921600))


def api_base() -> str:
    kb = kokoromo_url()
    mb = memory_backend()
    lu = llm_url()
    if mb in ("mem0", "none") or not kb:
        return lu + "/v1"
    return kb + "/v1"


# ── tool calling ────────────────────────────────────────────────────────────

def tool_enabled() -> bool:
    section = get("tool_calling", {})
    if not isinstance(section, dict):
        return False
    return bool(section.get("enabled", False))


def tool_list() -> list[str]:
    section = get("tool_calling", {})
    if not isinstance(section, dict):
        return ["look_at_screen", "search_memory", "get_current_time", "get_current_app"]
    return section.get("tools", ["look_at_screen", "search_memory", "get_current_time", "get_current_app"])


def tool_max_iterations() -> int:
    section = get("tool_calling", {})
    if not isinstance(section, dict):
        return 5
    return int(section.get("max_iterations", 5))


def tool_timeout() -> float:
    section = get("tool_calling", {})
    if not isinstance(section, dict):
        return 45.0
    return float(section.get("tool_timeout", 45.0))


def tool_router_model() -> str:
    section = get("tool_calling", {})
    if isinstance(section, dict):
        value = str(section.get("router_model", "") or "").strip()
        if value:
            return value
    return ""


# ── proactive dialogue ───────────────────────────────────────────────────────

def proactive_enabled() -> bool:
    section = get("proactive", {})
    if not isinstance(section, dict):
        return False
    return bool(section.get("enabled", False))


def inner_stream_config() -> dict:
    section = get("inner_stream", {})
    if not isinstance(section, dict):
        section = {}
    return section


def inner_stream_search_config() -> dict:
    section = get("inner_stream_search", {})
    if not isinstance(section, dict):
        section = {}
    return section


def autonomous_step_config() -> dict:
    section = get("autonomous_step", {})
    if not isinstance(section, dict):
        section = {}
    return section


def inner_memory_config() -> dict:
    section = get("inner_memory", {})
    if not isinstance(section, dict):
        section = {}
    return section


def inner_cognition_config() -> dict:
    section = get("inner_cognition", {})
    if not isinstance(section, dict):
        section = {}
    return section


# ── bilibili_live ────────────────────────────────────────────────────────────

def bilibili_live_enabled() -> bool:
    section = get("bilibili_live", {})
    if not isinstance(section, dict):
        return False
    return bool(section.get("enabled", False))


def bilibili_live_live_mode() -> bool:
    scene = get("scene", {})
    if isinstance(scene, dict) and "live_enabled" in scene:
        return bool(scene.get("live_enabled", False))
    section = get("bilibili_live", {})
    if not isinstance(section, dict):
        return False
    return bool(section.get("live_mode", False))


def bilibili_live_room_id() -> int:
    section = get("bilibili_live", {})
    if not isinstance(section, dict):
        return 0
    return int(section.get("room_id", 0))


def bilibili_live_buffer_max_age() -> float:
    section = get("bilibili_live", {})
    return float(section.get("buffer_max_age", 120.0))


# ── agent (Claude Code) ────────────────────────────────────────────────────

def agent_enabled() -> bool:
    section = get("agent", {})
    if not isinstance(section, dict):
        return False
    return bool(section.get("enabled", False))


def agent_claude_code_path() -> str:
    section = get("agent", {})
    if not isinstance(section, dict):
        return ""
    return str(section.get("claude_code_path", ""))


def agent_timeout() -> float:
    section = get("agent", {})
    if not isinstance(section, dict):
        return 120.0
    return float(section.get("timeout", 120.0))


def agent_working_dir() -> str:
    section = get("agent", {})
    if not isinstance(section, dict):
        return ""
    return str(section.get("working_dir", ""))
