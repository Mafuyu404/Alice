#!/usr/bin/env python3
"""Single-character voice CLI runtime."""

from __future__ import annotations

import argparse

from kokoro.action.tools import single_runtime as single_runtime_tool
from kokoro.action.cli_common import (
    CONFIG,
    _PROJECT_ROOT,
    display_user,
)


def run(args: argparse.Namespace) -> None:
    runtime = single_runtime_tool.create_cli_runtime(
        args=args,
        config=CONFIG,
        root=_PROJECT_ROOT,
        display_user=display_user,
        printer=print,
    )
    if runtime is None:
        return

    try:
        single_runtime_tool.start_cli_runtime(
            bundle=runtime,
            args=args,
            config=CONFIG,
            root=_PROJECT_ROOT,
            printer=print,
        )
        single_runtime_tool.run_until_shutdown(runtime.machine, printer=print)
    finally:
        single_runtime_tool.shutdown_cli_runtime_bundle(
            runtime,
            printer=print,
        )






