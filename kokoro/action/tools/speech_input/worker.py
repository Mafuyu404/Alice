"""Microphone worker loop for STT runtimes."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable

from kokoro.action.tools.speech_input import stt as stt_mod
from kokoro.core import config as cfg


def create_partial_handler(
    *,
    is_probable_tts_echo: Callable[[str], bool],
    machine,
    stt_subtitle_client=None,
) -> Callable[[str], None]:
    from kokoro.core import state_machine as sm

    def on_partial(text: str) -> None:
        if is_probable_tts_echo(text):
            if stt_subtitle_client:
                stt_subtitle_client.clear()
            return
        if stt_subtitle_client:
            stt_subtitle_client.push_text(text, mode="set")
        import sys

        sys.stdout.write(f"\r\033[K  [STT] {text}")
        sys.stdout.flush()
        if machine.is_idle or machine.state == sm.SystemState.SCREEN_WATCHING:
            machine.emit(sm.SystemEvent.USER_SPEECH_START)
            machine.set_stt_state(sm.STTState.LISTENING)

    return on_partial


def create_single_partial_handler(**kwargs) -> Callable[[str], None]:
    return create_partial_handler(**kwargs)


def start_default_worker(
    *,
    machine,
    device: int | None,
    conversation,
    aec_processor=None,
    tts_engine=None,
    tts_map: dict[str, object] | None = None,
    flush_callback: Callable[[], None] | None = None,
    error_source: str = "stt",
    print_error: Callable[[str], None] = print,
    pause_during_tts: bool | None = None,
) -> threading.Thread:
    if pause_during_tts is None:
        pause_during_tts = cfg.stt_pause_during_tts()
        if aec_processor is not None:
            pause_during_tts = False
    return start_worker(
        machine=machine,
        device=device,
        conversation=conversation,
        aec_processor=aec_processor,
        tts_engine=tts_engine,
        tts_map=tts_map,
        pause_during_tts=pause_during_tts,
        flush_callback=flush_callback,
        error_source=error_source,
        print_error=print_error,
    )


def start_worker(
    *,
    machine,
    device: int | None,
    conversation,
    aec_processor=None,
    tts_engine=None,
    tts_map: dict[str, object] | None = None,
    pause_during_tts: bool = False,
    flush_callback: Callable[[], None] | None = None,
    error_source: str = "stt",
    print_error: Callable[[str], None] = print,
) -> threading.Thread:
    def worker() -> None:
        import sounddevice as sd

        audio_stream = None
        try:
            audio_stream = sd.InputStream(
                device=device,
                channels=1,
                samplerate=stt_mod.SAMPLE_RATE,
                dtype="float32",
                blocksize=1600,
            )
            audio_stream.start()

            while not machine.is_shutting_down:
                try:
                    chunk, _ = audio_stream.read(1600)
                except Exception as exc:
                    if machine.is_shutting_down or "Invalid stream pointer" in str(exc):
                        break
                    raise

                if pause_during_tts:
                    if tts_engine is not None and getattr(tts_engine, "is_playing", False):
                        continue
                    if tts_map and any(engine and getattr(engine, "is_playing", False) for engine in tts_map.values()):
                        continue

                if aec_processor is not None:
                    mono = stt_mod.denoise(aec_processor.process(chunk[:, 0]))
                else:
                    mono = stt_mod.denoise(chunk[:, 0])

                conversation.feed_audio(mono)
                if flush_callback is not None:
                    flush_callback()
        except Exception as exc:
            print_error(f"\n[STT error] {exc}")
            traceback.print_exc()
            machine.emit_error(error_source)
        finally:
            if audio_stream is not None:
                try:
                    audio_stream.stop()
                    audio_stream.close()
                except Exception:
                    pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread
