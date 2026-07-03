"""TTS backend dispatcher."""

from __future__ import annotations

import importlib
import logging
import threading
import time

from kokoro.core import config as cfg

logger = logging.getLogger(__name__)

_BACKEND_NAME = cfg.tts_backend()
_BACKEND = None
_TTS_LOCK = threading.Lock()


def _get_backend():
    global _BACKEND
    if _BACKEND is None:
        module = importlib.import_module(f"kokoro.action.tools.say.tts_{_BACKEND_NAME}")
        _BACKEND = module
        info = getattr(module, "get_voices", lambda: {})()
        logger.info("TTS backend: %s", info.get("engine", _BACKEND_NAME))
    return _BACKEND


def __getattr__(name):
    return getattr(_get_backend(), name)


def __dir__():
    return dir(_get_backend())


def create_engine(enabled: bool, voice_id: str | None = None):
    if not enabled:
        return None
    try:
        backend = _get_backend()
        backend.warmup()
        return backend.StreamingTTS(voice=voice_id)
    except Exception as exc:
        print(f"  [cli] TTS init failed: {exc}")
        return None


def prepare_engine(engine) -> None:
    if engine is None:
        return
    try:
        t0 = time.perf_counter()
        if engine.prepare():
            print(f"  [tts] websocket ready ({time.perf_counter() - t0:.1f}s)")
    except Exception as exc:
        print(f"  [tts] warmup failed: {exc}")


def create_prepared_engine(enabled: bool, voice_id: str | None = None):
    engine = create_engine(enabled, voice_id)
    prepare_engine(engine)
    return engine


def create_engines_for_characters(
    character_ids: list[str],
    characters: dict,
    *,
    enabled: bool,
    printer=print,
) -> dict[str, object]:
    engines: dict[str, object] = {}
    if not enabled:
        return engines
    for character_id in character_ids:
        char_data = characters.get(character_id, {})
        voice_id = char_data.get("tts_voice_id") if isinstance(char_data, dict) else None
        try:
            engine = create_prepared_engine(True, voice_id)
            if engine is not None:
                engines[character_id] = engine
        except Exception as exc:
            printer("  [tts] init failed for " + character_id + ": " + str(exc))
    return engines


def say_text(engine, text: str, *, wait: bool = True) -> None:
    """Push text to a TTS engine. Shared by single and multi runtimes."""
    if engine is None:
        return
    with _TTS_LOCK:
        try:
            if not engine.prepare():
                return
            for line in str(text or "").splitlines():
                line = line.strip()
                if line:
                    engine.push(line)
            engine.end_sentence()
            if wait:
                while getattr(engine, "is_playing", False):
                    time.sleep(0.05)
        except Exception as exc:
            print("  [tts] error: " + str(exc))


def wait_for_engines(engines: dict[str, object] | list[object] | tuple[object, ...], cancel_event=None) -> None:
    values = engines.values() if isinstance(engines, dict) else engines
    while any(getattr(engine, "is_playing", False) for engine in values):
        if cancel_event and cancel_event.is_set():
            break
        time.sleep(0.05)
