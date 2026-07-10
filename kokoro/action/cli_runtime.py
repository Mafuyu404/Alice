#!/usr/bin/env python3
"""CLI runtime dispatcher."""

from __future__ import annotations

import sys
import time
import traceback

from kokoro.action.tools.speech_input import stt as stt_mod
from kokoro.action import single_cli_runtime, multi_cli_runtime
from kokoro.action.cli_common import _install_cli_log, get_args


def main() -> None:
    args = get_args()
    if args.list_devices:
        stt_mod.list_devices()
        return
    if args.output_mode == "text":
        import text_cli

        text_cli.main(args)
        return
    if args.output_mode == "life-debug":
        from scripts import run_life_runtime_debug

        raise SystemExit(run_life_runtime_debug.main(args))
    if args.multi:
        multi_cli_runtime.run(args)
        return
    single_cli_runtime.run(args)


def run() -> None:
    _cli_log, _stdout, _stderr = _install_cli_log()
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nBye.")
    except Exception as exc:
        print(f"\n[error] {type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        time.sleep(0.3)
        sys.stdout = _stdout
        sys.stderr = _stderr
        _cli_log.close()


if __name__ == "__main__":
    run()
