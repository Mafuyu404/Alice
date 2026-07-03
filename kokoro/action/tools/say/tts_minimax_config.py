"""MiniMax TTS configuration constants."""

from __future__ import annotations

from kokoro.core import config as cfg

MINIMAX_API_KEY = cfg.minimax_api_key()
MINIMAX_MODEL = cfg.minimax_model()
SAMPLE_RATE = int(cfg.get("minimax_sample_rate", 32000))
TTS_VOLUME = cfg.tts_volume()
WS_URL = "wss://api.minimax.io/ws/v1/t2a_v2"
WS_OPEN_TIMEOUT = 3
WS_CLOSE_TIMEOUT = 1
TASK_STARTED_TIMEOUT = 3
FAST_FAIL_TIMEOUT = 1.5
FAST_FAIL_RESET_SECONDS = 20.0
FAST_FAIL_AFTER_FAILURES = 2

VOICE_PRESETS = {
    "default": "Chinese (Mandarin)_Crisp_Girl",
    "crisp_girl": "Chinese (Mandarin)_Crisp_Girl",
    "qingse": "male-qn-qingse",
    "tianmei": "female-tianmei",
    "narrator_en": "English_expressive_narrator",
}


def resolve_voice(voice: str | None = None) -> str:
    if voice is None:
        return VOICE_PRESETS["default"]
    return VOICE_PRESETS.get(voice, voice)
