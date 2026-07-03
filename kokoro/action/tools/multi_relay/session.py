"""Session bootstrap for multi-character CLI runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MultiSessionRuntime:
    runtime_config: dict
    user_name: str
    model: str
    characters: dict
    orchestrator: object
    names: dict[str, str]
    aec_enabled: bool
    stt_enabled: bool


def load_session_runtime(
    *,
    character_ids: list[str],
    model_override: str | None = None,
) -> MultiSessionRuntime:
    from kokoro.action import action_policy
    from kokoro.action import multi_chat as multi_chat_mod
    from kokoro.core import character as char_mod
    from kokoro.core import config as cfg

    runtime_config = cfg.load()
    default_model = cfg.llm_model()
    model = model_override or default_model
    if "charglm" in model:
        model = default_model

    characters = char_mod.load()
    config = multi_chat_mod.MultiChatConfig(character_ids=character_ids, model=model)
    orchestrator = action_policy.MultiActorActionPolicy(config)
    return MultiSessionRuntime(
        runtime_config=runtime_config,
        user_name=cfg.user_name(),
        model=model,
        characters=characters,
        orchestrator=orchestrator,
        names=orchestrator.character_names,
        aec_enabled=cfg.aec_enabled(),
        stt_enabled=cfg.stt_enabled(),
    )
