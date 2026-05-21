#!/usr/bin/env python3
"""WebSocket bridge between NapCat OneBot and Alice QQ runtime.

NapCat exposes OneBot events on one WebSocket.  Alice connects to this bridge
as a client, receives those events, and sends OneBot actions such as
``send_msg`` back through the same bridge.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from kokoro import console as console_mod


logger = logging.getLogger("napcat_bridge")


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NapCat <-> Alice WebSocket bridge")
    parser.add_argument("--napcat-host", default="localhost", help="NapCat WebSocket host")
    parser.add_argument("--napcat-port", type=int, default=3001, help="NapCat WebSocket port")
    parser.add_argument("--bridge-host", default="127.0.0.1", help="Host for Alice clients")
    parser.add_argument("--bridge-port", type=int, default=58888, help="Port for Alice clients")
    parser.add_argument("--debug", action="store_true", help="Enable debug logs")
    return parser.parse_args()


class NapCatBridge:
    def __init__(self, args: argparse.Namespace) -> None:
        self.napcat_url = f"ws://{args.napcat_host}:{args.napcat_port}"
        self.bridge_host = args.bridge_host
        self.bridge_port = args.bridge_port
        self._alice_clients: set = set()
        self._napcat_ws = None
        self._running = False

    async def _on_napcat_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if "post_type" not in data or data.get("post_type") == "meta_event":
            return

        if self._alice_clients:
            dead = set()
            for ws in self._alice_clients:
                try:
                    await ws.send(raw)
                except Exception:
                    dead.add(ws)
            self._alice_clients -= dead

    async def _on_alice_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Alice sent non-JSON payload: %s", raw[:80])
            return
        action = data.get("action")
        params = data.get("params", {})
        if not action:
            logger.warning("Alice payload missing action: %s", raw[:80])
            return
        if self._napcat_ws is None:
            logger.warning("NapCat is not connected; dropping action=%s", action)
            return
        await self._napcat_ws.send(json.dumps({"action": action, "params": params}, ensure_ascii=False))

    async def _connect_napcat(self) -> None:
        import websockets

        while self._running:
            try:
                async with websockets.connect(self.napcat_url) as ws:
                    self._napcat_ws = ws
                    logger.info("connected to NapCat: %s", self.napcat_url)
                    async for raw in ws:
                        await self._on_napcat_message(raw)
            except (OSError, ConnectionRefusedError):
                logger.warning("NapCat is not ready; retrying in 5 seconds")
                await asyncio.sleep(5)
            except Exception as exc:
                logger.error("NapCat connection failed: %s", exc)
                await asyncio.sleep(5)
            finally:
                self._napcat_ws = None

    async def _serve_alice(self) -> None:
        from websockets.asyncio.server import serve

        async def handler(ws):
            self._alice_clients.add(ws)
            logger.info("Alice connected: %s", ws.remote_address)
            try:
                async for raw in ws:
                    await self._on_alice_message(raw)
            finally:
                self._alice_clients.discard(ws)
                logger.info("Alice disconnected: %s", ws.remote_address)

        async with serve(handler, self.bridge_host, self.bridge_port):
            logger.info("waiting for Alice: ws://%s:%d", self.bridge_host, self.bridge_port)
            await asyncio.Future()

    async def run(self) -> None:
        self._running = True
        logger.info("bridge started: %s <-> ws://%s:%d", self.napcat_url, self.bridge_host, self.bridge_port)
        await asyncio.gather(self._connect_napcat(), self._serve_alice())


async def main() -> None:
    console_mod.ensure_utf8_console()
    args = get_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="[napcat-bridge] %(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    bridge = NapCatBridge(args)
    try:
        await bridge.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())
