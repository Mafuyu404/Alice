"""Session bootstrap for single-character CLI runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kokoro.core import chat_session
from kokoro.core import config as cfg
from kokoro.core import memory as mem_mod


@dataclass
class SingleSessionRuntime:
    memory_backend: object
    session: object | None
    dialogue_model: str = ""
    aec_enabled: bool = False
    stt_refine_inline: bool = False
    stt_dialogue_pool_enabled: bool = False
    proactive_enabled: bool = False
    stt_enabled: bool = False


def load_session_runtime(
    *,
    character_id: str,
    config: dict,
    root: Path,
    model_override: str | None = None,
    no_proactive: bool = False,
    no_stt: bool = False,
    printer=print,
) -> SingleSessionRuntime:
    memory_backend = mem_mod.create_backend(config)
    try:
        session = chat_session.load_session(character_id, memory_backend)
    except KeyError:
        from kokoro.core import character

        printer(f"[error] Character '{character_id}' not found")
        printer(f"Available characters: {', '.join(character.load().keys())}")
        return SingleSessionRuntime(memory_backend=memory_backend, session=None)

    summary_dir = str(root / "data")
    session.summary_file = str(Path(summary_dir) / f"summary_{character_id}.json")
    session.load_summary()
    dialogue_model = model_override or session.character_config.get("llm_model") or cfg.dialogue_model()
    return SingleSessionRuntime(
        memory_backend=memory_backend,
        session=session,
        dialogue_model=dialogue_model,
        aec_enabled=cfg.aec_enabled(),
        stt_refine_inline=cfg.stt_refine_mode() == "inline",
        stt_dialogue_pool_enabled=cfg.stt_dialogue_pool_enabled(),
        proactive_enabled=cfg.proactive_enabled() and not no_proactive,
        stt_enabled=cfg.stt_enabled() and not no_stt,
    )
