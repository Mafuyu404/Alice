"""Lifecycle helper for the optional open-websearch daemon."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from kokoro.action.tools.search_web.client import WebSearchClient


@dataclass
class WebSearchDaemonRuntime:
    process: subprocess.Popen | None = None

    def stop(self) -> None:
        stop(self.process)


def start_runtime(config: dict, *, root: Path | None = None) -> WebSearchDaemonRuntime:
    return WebSearchDaemonRuntime(process=start(config, root=root))


def start(config: dict, *, root: Path | None = None) -> subprocess.Popen | None:
    section = config.get("inner_stream_search", {})
    if not isinstance(section, dict) or not bool(section.get("enabled", False)):
        return None
    base_url = str(section.get("base_url") or "http://127.0.0.1:58902").rstrip("/")
    try:
        WebSearchClient(base_url=base_url, timeout=2.0).health()
        print(f"  [web_search] daemon ready: {base_url}")
        return None
    except Exception:
        pass

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 58902
    node_exe, entry_js = _find_open_websearch_entry()
    if not node_exe or not entry_js:
        print("  [web_search] open-websearch not found; install with `npm install -g open-websearch`")
        return None

    root = root or Path.cwd()
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path = log_dir / "open-websearch.out.log"
    err_path = log_dir / "open-websearch.err.log"
    out_file = out_path.open("a", encoding="utf-8")
    err_file = err_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [node_exe, entry_js, "serve", "--host", host, "--port", str(port)],
            cwd=str(root),
            stdout=out_file,
            stderr=err_file,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        out_file.close()
        err_file.close()
        print(f"  [web_search] failed to start daemon: {type(exc).__name__}: {exc}")
        return None

    for _ in range(20):
        time.sleep(0.5)
        try:
            WebSearchClient(base_url=base_url, timeout=2.0).health()
            print(f"  [web_search] daemon started: {base_url}")
            return proc
        except Exception:
            if proc.poll() is not None:
                print(f"  [web_search] daemon exited early; see {err_path}")
                return None
    print(f"  [web_search] daemon start timed out: {base_url}")
    return proc


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _find_open_websearch_entry() -> tuple[str, str]:
    candidates: list[tuple[str, str]] = []
    for base in (
        os.path.dirname(sys.executable),
        os.environ.get("APPDATA", ""),
        r"D:\program\nodejs",
    ):
        if not base:
            continue
        candidates.append(
            (
                os.path.join(base, "node.exe"),
                os.path.join(base, "node_modules", "open-websearch", "build", "index.js"),
            )
        )
    for node_exe, entry_js in candidates:
        if os.path.exists(node_exe) and os.path.exists(entry_js):
            return node_exe, entry_js
    return "", ""
