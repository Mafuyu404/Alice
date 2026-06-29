from __future__ import annotations

import locale
import os
import subprocess
import sys


def ensure_utf8_console() -> None:
    try:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["chcp", "65001"],
                    check=False,
                    capture_output=True,
                    shell=True,
                )
            except Exception:
                pass
            try:
                locale.setlocale(locale.LC_ALL, "")
            except Exception:
                pass
            os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
