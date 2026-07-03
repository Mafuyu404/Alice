"""QQ transport bridge runtime."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from typing import Callable

from kokoro.action.tools.qq import input as qq_input
from kokoro.action.tools import say as say_tool

QQToolSender = Callable[[str], str]


@dataclass
class QQBridge:
    enabled: bool
    runtime: qq_input.QQInputRuntime | None = None
    host: str = "127.0.0.1"
    port: int = 58901
    character_name: str = ""
    stop_event: threading.Event | None = None
    thread: threading.Thread | None = None
    tool_sender: list[Callable[..., str] | None] = field(default_factory=lambda: [None])

    def start(self) -> None:
        if not self.enabled or self.runtime is None:
            return
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run_server, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def send_message(self, *args, **kwargs) -> str:
        sender = self.tool_sender[0]
        if sender is None:
            return "QQ sender unavailable"
        return sender(*args, **kwargs)

    def _run_server(self) -> None:
        async def _serve() -> None:
            from websockets.asyncio.server import serve

            assert self.runtime is not None
            assert self.stop_event is not None
            clients: set = set()
            loop = asyncio.get_running_loop()
            poll_lock = threading.Lock()

            async def _send_action(ws, action: str, params: dict) -> None:
                await ws.send(json.dumps({"action": action, "params": params}, ensure_ascii=False))

            def _send_qq_message_from_llm(
                message: str,
                *,
                conversation_id: str = "",
                reason: str = "llm_decided",
            ) -> str:
                target_id = (conversation_id or "").strip() or self.runtime.recent_conversation_id()
                if not target_id:
                    return "QQ send failed: no recent conversation."

                params = {"message": message}
                if target_id.startswith("group:"):
                    params["message_type"] = "group"
                    params["group_id"] = target_id.split(":", 1)[1]
                elif target_id.startswith("private:"):
                    params["message_type"] = "private"
                    params["user_id"] = target_id.split(":", 1)[1]
                else:
                    return f"QQ send failed: unknown conversation_id {target_id!r}."

                decision = qq_input.QQParticipationDecision(
                    action="say",
                    conversation_id=target_id,
                    message=message,
                    reason=reason,
                )
                future = asyncio.run_coroutine_threadsafe(_send_action(ws, "send_msg", params), loop)
                try:
                    future.result(timeout=5)
                except Exception as exc:
                    return f"QQ send failed: {type(exc).__name__}: {exc}"
                self.runtime.record_sent(decision, self_id=self.runtime.self_id, nickname=self.character_name)
                print(f"\n[qq] tool say {target_id}: {message[:80]}")
                return f"QQ message sent to {target_id}: {message}"

            def _poll_and_maybe_send(ws) -> None:
                if not poll_lock.acquire(blocking=False):
                    return
                try:
                    try:
                        decision = self.runtime.poll()
                    except Exception as exc:
                        print(f"\n[qq] poll failed: {type(exc).__name__}: {exc}")
                        return
                    if decision.action != "say":
                        if decision.reason:
                            print(f"\n[qq] silent: {decision.reason}")
                        return
                    params = {"message": decision.payload}
                    if decision.conversation_id.startswith("group:"):
                        params["message_type"] = "group"
                        params["group_id"] = decision.conversation_id.split(":", 1)[1]
                    elif decision.conversation_id.startswith("private:"):
                        params["message_type"] = "private"
                        params["user_id"] = decision.conversation_id.split(":", 1)[1]
                    else:
                        print(f"\n[qq] unknown conversation id: {decision.conversation_id}")
                        return

                    self.runtime.record_sent(decision, self_id=self.runtime.self_id, nickname=self.character_name)
                    future = asyncio.run_coroutine_threadsafe(_send_action(ws, "send_msg", params), loop)

                    def _log_send_result(done) -> None:
                        try:
                            done.result()
                        except Exception as exc:
                            print(f"\n[qq] send action failed: {type(exc).__name__}: {exc}")
                            return
                        print(f"\n[qq] say {decision.conversation_id}: {decision.message[:80]}")

                    future.add_done_callback(_log_send_result)
                finally:
                    poll_lock.release()

            async def handler(ws) -> None:
                clients.add(ws)
                self.tool_sender[0] = _send_qq_message_from_llm
                print(f"\n[qq] transport connected: {ws.remote_address}")
                stop_client = asyncio.Event()

                async def periodic_poll() -> None:
                    interval = max(0.5, min(2.0, float(getattr(self.runtime, "batch_quiet_seconds", 4.0)) / 2.0))
                    while not stop_client.is_set() and not self.stop_event.is_set():
                        await asyncio.sleep(interval)
                        if stop_client.is_set() or self.stop_event.is_set():
                            break
                        threading.Thread(target=_poll_and_maybe_send, args=(ws,), daemon=True).start()

                poll_task = asyncio.create_task(periodic_poll())
                try:
                    async for raw in ws:
                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        event = payload.get("event") if isinstance(payload, dict) else None
                        if not isinstance(event, dict):
                            continue
                        message = self.runtime.ingest_onebot_event(event)
                        if message is not None:
                            print(f"\n[qq] {message.conversation_id} {message.nickname}: {message.content[:80]}")
                        threading.Thread(target=_poll_and_maybe_send, args=(ws,), daemon=True).start()
                finally:
                    stop_client.set()
                    poll_task.cancel()
                    try:
                        await poll_task
                    except asyncio.CancelledError:
                        pass
                    clients.discard(ws)
                    if not clients:
                        self.tool_sender[0] = None
                    print("\n[qq] transport disconnected")

            async with serve(handler, self.host, self.port):
                print(f"  [qq] input server: ws://{self.host}:{self.port}")
                while not self.stop_event.is_set():
                    await asyncio.sleep(0.2)
                for ws in list(clients):
                    await ws.close()

        try:
            asyncio.run(_serve())
        except Exception as exc:
            print(f"\n[qq] input server stopped: {type(exc).__name__}: {exc}")


def create(
    *,
    enabled: bool,
    session,
    model: str | None,
    config: dict,
    host: str,
    port: int,
) -> QQBridge:
    runtime = qq_input.QQInputRuntime(session=session, model=model, config=config) if enabled else None
    return QQBridge(
        enabled=enabled,
        runtime=runtime,
        host=host,
        port=port,
        character_name=getattr(session, "character_name", ""),
    )


def create_from_cli(
    *,
    args,
    session,
    config: dict,
) -> QQBridge:
    section = config.get("qq", {})
    if not isinstance(section, dict):
        section = {}
    enabled = bool(getattr(args, "qq", False) or section.get("enabled", False))
    host = getattr(args, "qq_host", None) or str(section.get("alice_host") or "127.0.0.1")
    port = int(getattr(args, "qq_port", None) or section.get("alice_port") or 58901)
    model = str(section.get("participation_model", "") or "").strip() or None
    return create(
        enabled=enabled,
        session=session,
        model=model,
        config=section,
        host=host,
        port=port,
    )


def looks_like_message_request(text: str) -> bool:
    raw = text or ""
    compact = say_tool.normalize_echo_text(raw)
    if "qq" not in compact and "q" not in compact:
        return False
    return any(marker in raw for marker in ("消息", "群", "聊天", "看", "收到", "发"))


def boundary_reply_for_text(text: str) -> str:
    if not looks_like_message_request(text):
        return ""
    return "QQ 消息流还没接上，我现在不能真的发到群里。"
