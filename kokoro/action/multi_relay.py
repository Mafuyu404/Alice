"""Multi-character relay server.

CLI instances connect as different characters and exchange messages
through this relay. Each instance handles its own TTS, portrait, STT —
the relay just forwards text.

Protocol (TCP, newline-delimited)::

  → REGISTER:<character_name>  — identify on connect
  ← HELLO:<your_name>           — server confirms registration
  → MESSAGE:<text>              — send a message (speaker = registered name)
  ← MESSAGE:<speaker>:<text>    — receive a message from another character
  → PING / ← PONG               — keepalive
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class RelayClient:
    name: str
    conn: socket.socket
    addr: tuple


class MultiRelayServer:
    """TCP relay that broadcasts messages between connected CLI instances."""

    def __init__(self, host: str = "127.0.0.1", port: int = 19412):
        self.host = host
        self.port = port
        self._clients: dict[str, RelayClient] = {}  # name → client
        self._lock = threading.Lock()
        self._server: socket.socket | None = None
        self._running = False

    def start(self) -> None:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self.port))
        self._server.listen(5)
        self._server.settimeout(1.0)
        self._running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        logger.info("relay listening on %s:%d", self.host, self.port)

    def stop(self) -> None:
        self._running = False
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
        with self._lock:
            for client in list(self._clients.values()):
                try:
                    client.conn.close()
                except Exception:
                    pass
            self._clients.clear()

    def broadcast(self, speaker: str, text: str, exclude: str | None = None) -> None:
        """Send a message to all connected clients except the speaker."""
        msg = f"MESSAGE:{speaker}:{text}\n"
        with self._lock:
            for name, client in list(self._clients.items()):
                if name == exclude:
                    continue
                try:
                    client.conn.sendall(msg.encode("utf-8"))
                except Exception:
                    logger.warning("relay: failed to send to %s, removing", name)
                    try:
                        client.conn.close()
                    except Exception:
                        pass
                    del self._clients[name]

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, addr = self._server.accept()
                threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
            except Exception as exc:
                if self._running:
                    logger.warning("relay accept error: %s", exc)

    def _handle_client(self, conn: socket.socket, addr: tuple) -> None:
        conn.settimeout(30.0)
        buf = ""
        name = "unknown"

        try:
            # First line must be REGISTER
            data = conn.recv(4096).decode("utf-8")
            buf += data
            line, _, buf = buf.partition("\n")
            if not line.startswith("REGISTER:"):
                conn.sendall(b"ERROR:expected REGISTER\n")
                return
            name = line[len("REGISTER:"):].strip()
            if not name:
                conn.sendall(b"ERROR:empty name\n")
                return

            with self._lock:
                self._clients[name] = RelayClient(name=name, conn=conn, addr=addr)
            conn.sendall(f"HELLO:{name}\n".encode("utf-8"))
            logger.info("relay: %s (%s:%d) registered", name, addr[0], addr[1])

            # Message loop
            while self._running:
                data = conn.recv(4096).decode("utf-8")
                if not data:
                    break
                buf += data
                while "\n" in buf:
                    line, _, buf = buf.partition("\n")
                    line = line.strip()
                    if not line:
                        continue
                    if line == "PING":
                        conn.sendall(b"PONG\n")
                    elif line.startswith("MESSAGE:"):
                        text = line[len("MESSAGE:"):]
                        self.broadcast(name, text, exclude=name)
                    else:
                        logger.debug("relay: unknown line from %s: %s", name, line[:60])

        except socket.timeout:
            logger.info("relay: %s timed out", name)
        except Exception as exc:
            logger.info("relay: %s disconnected (%s)", name, exc)
        finally:
            with self._lock:
                if name in self._clients:
                    del self._clients[name]
            try:
                conn.close()
            except Exception:
                pass
            logger.info("relay: %s left (%d remaining)", name, len(self._clients))


# ── CLI-side relay client ──────────────────────────────────────────────────


class RelayClientConn:
    """Connection from a CLI instance to the relay server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 17520,
                 on_message: Callable[[str, str], None] | None = None):
        self.host = host
        self.port = port
        self.on_message = on_message  # callback(speaker, text)
        self._sock: socket.socket | None = None
        self._running = False
        self._recv_thread: threading.Thread | None = None
        self._name: str = ""
        self._owned_server: MultiRelayServer | None = None

    def connect(self, character_name: str) -> bool:
        """Connect to relay and register. Returns True on success."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(10.0)
            self._sock.connect((self.host, self.port))
            self._sock.sendall(f"REGISTER:{character_name}\n".encode("utf-8"))
            data = self._sock.recv(4096).decode("utf-8")
            if data.startswith("HELLO:"):
                self._name = character_name
                self._running = True
                self._sock.settimeout(None)  # blocking mode for recv loop
                self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
                self._recv_thread.start()
                return True
            logger.warning("relay: registration rejected: %s", data.strip())
            return False
        except Exception as exc:
            logger.warning("relay: connect failed: %s", exc)
            return False

    def send_message(self, text: str) -> None:
        """Send a message to other characters via the relay."""
        if self._sock and self._running:
            try:
                self._sock.sendall(f"MESSAGE:{text}\n".encode("utf-8"))
            except Exception:
                pass  # connection lost — will reconnect on next attempt

    @classmethod
    def connect_or_start(cls, port: int = 19412, host: str = "127.0.0.1",
                         on_message: Callable[[str, str], None] | None = None,
                         character_name: str = "") -> "RelayClientConn":
        """Try to connect; if no relay server, start one, then connect."""
        client = cls(host=host, port=port, on_message=on_message)
        # Retry a few times in case server just started
        for attempt in range(3):
            if client.connect(character_name):
                return client
            import time
            time.sleep(0.3 * (attempt + 1))
        # No server yet — start one
        try:
            server = MultiRelayServer(host=host, port=port)
            server.start()
        except OSError:
            # Port might be in use from a previous instance — try another port
            import random
            fallback = port + random.randint(1, 100)
            logger.info("relay: port %d busy, trying %d", port, fallback)
            server = MultiRelayServer(host=host, port=fallback)
            server.start()
            port = fallback
        import time
        time.sleep(0.5)
        for attempt in range(3):
            if client.connect(character_name):
                client._owned_server = server
                return client
            time.sleep(0.3 * (attempt + 1))
        raise ConnectionError(f"Cannot start or connect to relay on {host}:{port}")

    def close(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        if hasattr(self, '_owned_server') and self._owned_server:
            self._owned_server.stop()

    def _recv_loop(self) -> None:
        buf = ""
        while self._running:
            try:
                data = self._sock.recv(4096).decode("utf-8")
                if not data:
                    break
                buf += data
                while "\n" in buf:
                    line, _, buf = buf.partition("\n")
                    line = line.strip()
                    if not line:
                        continue
                    if line == "PONG":
                        continue
                    if line.startswith("MESSAGE:"):
                        rest = line[len("MESSAGE:"):]
                        colon = rest.find(":")
                        if colon > 0:
                            speaker = rest[:colon]
                            text = rest[colon + 1:]
                            if self.on_message:
                                self.on_message(speaker, text)
            except socket.timeout:
                continue
            except Exception:
                break
