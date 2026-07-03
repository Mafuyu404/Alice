#!/usr/bin/env python3
"""Multi-character CLI runtime."""

from __future__ import annotations

import argparse

from kokoro.action.tools import multi_relay as multi_relay_tool
from kokoro.action.cli_common import (
    CONFIG,
    _PROJECT_ROOT,
)


def run(args: argparse.Namespace) -> None:
    """Multi-character chat with TTS + portrait."""
    runtime = multi_relay_tool.create_cli_runtime(
        args=args,
        root=_PROJECT_ROOT,
        cli_config=CONFIG,
        printer=print,
    )
    if runtime is None:
        return

    try:
        try:
            multi_relay_tool.start_cli_runtime(
                bundle=runtime,
                args=args,
                printer=print,
            )
        except KeyboardInterrupt:
            pass
    finally:
        multi_relay_tool.shutdown_cli_runtime_bundle(
            runtime,
            printer=print,
        )




