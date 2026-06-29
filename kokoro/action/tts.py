"""TTS backend dispatcher."""

from __future__ import annotations

import importlib
import logging

from kokoro.core import config as cfg

logger = logging.getLogger(__name__)

_BACKEND_NAME = cfg.tts_backend()
_BACKEND = None


def _get_backend():
    global _BACKEND
    if _BACKEND is None:
        module = importlib.import_module(f"kokoro.tts_{_BACKEND_NAME}")
        _BACKEND = module
        info = getattr(module, "get_voices", lambda: {})()
        logger.info("TTS backend: %s", info.get("engine", _BACKEND_NAME))
    return _BACKEND


def __getattr__(name):
    return getattr(_get_backend(), name)


def __dir__():
    return dir(_get_backend())
