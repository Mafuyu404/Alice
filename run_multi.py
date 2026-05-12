"""Multi-character chat launcher.

Starts two CLI instances that auto-connect via the built-in relay.
Each instance handles its own TTS, STT, and portrait independently.
"""

import subprocess
import sys
import time

RELAY_PORT = 19412


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Multi-character chat launcher")
    parser.add_argument("--char1", default="penglai", help="First character ID")
    parser.add_argument("--char2", default="alice", help="Second character ID")
    parser.add_argument("--port", type=int, default=RELAY_PORT, help="Relay port (auto-started by first instance)")
    parser.add_argument("--no-tts", action="store_true", help="Disable TTS")
    parser.add_argument("--no-portrait", action="store_true", help="Disable portrait")
    args = parser.parse_args()

    cli_flags = f"--multi-port {args.port}"
    if args.no_tts:
        cli_flags += " --no-tts"
    if args.no_portrait:
        cli_flags += " --no-portrait"

    # Start two CLI instances — first one auto-starts the relay
    procs = []
    for cid in [args.char1, args.char2]:
        p = subprocess.Popen(
            [sys.executable, "cli.py", "--character", cid] + cli_flags.split(),
        )
        procs.append(p)
        time.sleep(2)  # stagger startups

    print(f"\n  Relay: auto (port {args.port})")
    print(f"  {args.char1} + {args.char2}")
    print("  Press Ctrl+C to stop all\n")

    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.kill()


if __name__ == "__main__":
    main()
