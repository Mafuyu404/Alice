"""Portrait catalog loading."""

from __future__ import annotations

import json
import logging

from kokoro.action.tools.say.portrait_config import ROOT, SHARED_PORTRAITS_FILE

logger = logging.getLogger(__name__)


def load_portrait_notes(character_id: str) -> list[dict]:
    if SHARED_PORTRAITS_FILE.exists():
        try:
            data = json.loads(SHARED_PORTRAITS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                items = data.get(character_id, [])
                if isinstance(items, list):
                    return items
                if isinstance(items, dict):
                    return items.get("portraits", items.get("assets", []))
        except Exception as exc:
            logger.warning("failed to load shared portrait notes for %s: %s", character_id, exc)

    path = ROOT / "characters" / character_id / "portrait" / "portrait.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("portraits", [])
    except Exception as exc:
        logger.warning("failed to load portrait notes for %s: %s", character_id, exc)
        return []
