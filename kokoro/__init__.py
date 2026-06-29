"""Kokoro runtime package.

Implementation modules live only under two packages:

- ``kokoro.core``: lifecycle state, events, memory/cognition/emotion, config,
  prompts, session state, and LLM clients.
- ``kokoro.action``: executable capabilities, IO channels, tool routing,
  speech/vision/QQ/VTS/search, and action execution.
"""

from __future__ import annotations

__all__ = ["core", "action"]
