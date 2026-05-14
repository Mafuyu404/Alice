"""Multi-character voice chat launcher."""

import subprocess
import sys


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Multi-character chat launcher")
    parser.add_argument("--chars", default="alice,penglai", help="Comma-separated character IDs")
    parser.add_argument("--char1", default=None, help="First character ID (legacy shortcut)")
    parser.add_argument("--char2", default=None, help="Second character ID (legacy shortcut)")
    parser.add_argument("--auto", type=int, default=0, help="Initial auto turns before watch/interactive mode")
    parser.add_argument("--watch", action="store_true", help="Keep characters talking unattended")
    parser.add_argument("--idle-seconds", type=float, default=0.6, help="Seconds between unattended turns")
    parser.add_argument("--max-turns", type=int, default=0, help="Maximum unattended turns; 0 means unlimited")
    parser.add_argument("--topic", default=None, help="Opening topic for watch/auto mode")
    parser.add_argument("--no-tts", action="store_true", help="Disable TTS")
    parser.add_argument("--no-portrait", action="store_true", help="Disable portrait")
    parser.add_argument("--model", default=None, help="Chat model override")
    args = parser.parse_args()

    chars = args.chars
    if args.char1 or args.char2:
        chars = ",".join([args.char1 or "alice", args.char2 or "penglai"])

    cmd = [sys.executable, "cli.py", "--multi", chars, "--auto", str(max(0, args.auto))]
    if args.watch:
        cmd.append("--watch")
    cmd.extend(["--idle-seconds", str(args.idle_seconds)])
    if args.max_turns:
        cmd.extend(["--max-turns", str(args.max_turns)])
    if args.topic:
        cmd.extend(["--topic", args.topic])
    if args.no_tts:
        cmd.append("--no-tts")
    if args.no_portrait:
        cmd.append("--no-portrait")
    if args.model:
        cmd.extend(["--model", args.model])

    print()
    print("  Multi-character voice chat")
    print("  Characters: " + chars)
    print("  Command: " + " ".join(cmd))
    print("  Press Ctrl+C to stop")
    print()

    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
