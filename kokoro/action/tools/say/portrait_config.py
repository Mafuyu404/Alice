"""Portrait overlay paths and defaults."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OVERLAY_SCRIPT = ROOT / "overlay_slideshow.py"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17352
SHARED_PORTRAITS_FILE = ROOT / "characters" / "portraits.json"
