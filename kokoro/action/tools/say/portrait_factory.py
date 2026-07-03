"""Portrait controller factories."""

from __future__ import annotations

import logging

from kokoro.core import config as cfg
from kokoro.action.tools.say.portrait_client import PortraitOverlayClient
from kokoro.action.tools.say.portrait_config import DEFAULT_HOST, DEFAULT_PORT
from kokoro.action.tools.say.portrait_worker import PortraitDecisionWorker

logger = logging.getLogger(__name__)


def create_controller(
    character_id: str,
    model: str,
    *,
    port: int | None = None,
    slot_index: int | None = None,
    slot_count: int = 1,
    state_file: str = "",
) -> tuple[PortraitOverlayClient, PortraitDecisionWorker]:
    host = cfg.get("portrait_overlay_host", DEFAULT_HOST)
    overlay_port = int(port if port is not None else cfg.get("portrait_overlay_port", DEFAULT_PORT))
    client = PortraitOverlayClient(
        host=host,
        port=overlay_port,
        character_id=character_id,
        slot_index=slot_index,
        slot_count=slot_count,
        state_file=state_file,
    )
    client.start()
    portrait_model = cfg.portrait_model()
    worker = PortraitDecisionWorker(client=client, model=portrait_model or model, character_id=character_id)
    return client, worker


def create_default_controller(
    *,
    enabled: bool,
    character_id: str,
    model: str,
    machine=None,
) -> tuple[PortraitOverlayClient | None, PortraitDecisionWorker | None]:
    if not enabled:
        return None, None
    try:
        client, worker = create_controller(character_id, model)
        if machine is not None:
            try:
                from kokoro.core import state_machine as sm

                machine.set_portrait_state(sm.PortraitState.SLIDESHOW)
            except Exception as exc:
                logger.debug("failed to set portrait state: %s", exc)
        return client, worker
    except Exception as exc:
        print(f"  [cli] Portrait overlay init failed: {exc}")
        return None, None


def create_multi_controllers(
    *,
    enabled: bool,
    character_ids: list[str],
    model: str,
    config: dict,
    printer=print,
) -> tuple[dict[str, PortraitOverlayClient], dict[str, PortraitDecisionWorker]]:
    clients: dict[str, PortraitOverlayClient] = {}
    workers: dict[str, PortraitDecisionWorker] = {}
    if not enabled:
        return clients, workers

    base_port = int(config.get("portrait_overlay_port", DEFAULT_PORT)) + 1
    slot_count = len(character_ids)
    for idx, character_id in enumerate(character_ids):
        try:
            client, worker = create_controller(
                character_id,
                model,
                port=base_port + idx,
                slot_index=idx,
                slot_count=slot_count,
                state_file="portrait_overlay_state_" + character_id + ".json",
            )
            clients[character_id] = client
            workers[character_id] = worker
        except Exception as exc:
            printer("  [portrait] init failed for " + character_id + ": " + str(exc))
    return clients, workers
