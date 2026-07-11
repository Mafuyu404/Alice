#!/usr/bin/env python3
"""Pure QQ transport for Alice.

This process owns no character state and runs no LLM.  It only bridges:

NapCat OneBot WebSocket <-> Alice local QQ WebSocket server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from kokoro import config as cfg
from kokoro.core import console as console_mod


logger = logging.getLogger("qq_client")


def get_args() -> argparse.Namespace:
    qq_section = cfg.get("qq", {})
    if not isinstance(qq_section, dict):
        qq_section = {}
    parser = argparse.ArgumentParser(description="QQ transport bridge for Alice")
    parser.add_argument(
        "--ws",
        dest="napcat_ws_url",
        default=str(qq_section.get("napcat_ws_url") or "ws://127.0.0.1:3001"),
        help="NapCat OneBot WebSocket URL",
    )
    parser.add_argument(
        "--alice",
        dest="alice_ws_url",
        default=str(qq_section.get("alice_ws_url") or "ws://127.0.0.1:58901"),
        help="Alice local QQ WebSocket URL",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logs")
    return parser.parse_args()


class QQTransport:
    def __init__(self, args: argparse.Namespace) -> None:
        self.napcat_ws_url = args.napcat_ws_url
        self.alice_ws_url = args.alice_ws_url
        self._napcat_ws = None
        self._alice_ws = None

    async def run(self) -> None:
        while True:
            try:
                await self._run_once()
            except (OSError, ConnectionRefusedError):
                logger.warning("NapCat or Alice WS is not ready; retrying in 5 seconds")
                await asyncio.sleep(5)
            except Exception as exc:
                logger.error("transport failed: %s", exc)
                await asyncio.sleep(5)

    async def _run_once(self) -> None:
        import websockets

        async with websockets.connect(self.napcat_ws_url) as napcat_ws:
            async with websockets.connect(self.alice_ws_url) as alice_ws:
                self._napcat_ws = napcat_ws
                self._alice_ws = alice_ws
                logger.info("connected: NapCat %s <-> Alice %s", self.napcat_ws_url, self.alice_ws_url)
                await asyncio.gather(
                    self._napcat_to_alice(),
                    self._alice_to_napcat(),
                )

    async def _napcat_to_alice(self) -> None:
        async for raw in self._napcat_ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "post_type" not in data or data.get("post_type") == "meta_event":
                continue
            await self._alice_ws.send(json.dumps({"kind": "onebot_event", "event": data}, ensure_ascii=False))

    async def _alice_to_napcat(self) -> None:
        async for raw in self._alice_ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Alice sent non-JSON payload: %s", raw[:80])
                continue
            action = data.get("action")
            params = data.get("params", {})
            if not action:
                logger.warning("Alice payload missing action: %s", raw[:80])
                continue
            logger.info("Alice -> NapCat action=%s params=%s", action, params)
            await self._napcat_ws.send(json.dumps({"action": action, "params": params}, ensure_ascii=False))


async def main() -> None:
    console_mod.ensure_utf8_console()
    args = get_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="[qq-client] %(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    await QQTransport(args).run()


if __name__ == "__main__":
    asyncio.run(main())
