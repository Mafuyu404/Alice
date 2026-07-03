"""Transport startup for multi-character CLI runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MultiToolTransports:
    web_search_runtime: object


def start_transports(*, config: dict, root) -> MultiToolTransports:
    from kokoro.action.tools import search_web as search_web_tool

    return MultiToolTransports(
        web_search_runtime=search_web_tool.start_runtime(config, root=root),
    )
