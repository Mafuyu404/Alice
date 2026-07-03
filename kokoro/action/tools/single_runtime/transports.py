"""Cross-tool transports for single-character CLI runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kokoro.action.tools import background as background_tool
from kokoro.action.tools import live as live_tool
from kokoro.action.tools import qq as qq_tool
from kokoro.action.tools import search_web as search_web_tool
from kokoro.action.tools import vts as vts_tool


@dataclass
class SingleToolTransports:
    vts_runtime: object
    live_runtime: object
    web_search_runtime: object
    qq_bridge: object
    screen_vision_timeout: int
    background_runtime: object | None = None

    def vts_say_resource_kwargs(self) -> dict[str, object | None]:
        return self.vts_runtime.say_resource_kwargs()

    def qq_send_message(self, *args, **kwargs):
        return self.qq_bridge.send_message(*args, **kwargs)

    def boundary_reply_for_text(self, text: str) -> str:
        return qq_tool.boundary_reply_for_text(text)

    def augment_live_context(self, command_context: str) -> str:
        return self.live_runtime.augment_context(command_context)

    def start_qq(self) -> None:
        self.qq_bridge.start()

    def start_background(
        self,
        *,
        machine,
        config: dict,
        no_screen_watch: bool,
        memory_backend,
        session,
        output_resources,
        dialogue,
        use_proactive,
    ) -> object:
        self.background_runtime = background_tool.start_default_runtime(
            machine=machine,
            config=config,
            no_screen_watch=no_screen_watch,
            memory_backend=memory_backend,
            session=session,
            tts_engine=output_resources.tts_engine,
            dialogue=dialogue,
            use_proactive=use_proactive,
        )
        return self.background_runtime


def start_transports(
    *,
    args,
    config: dict,
    root: Path,
    character_id: str,
    session,
    output_resources,
    machine,
) -> SingleToolTransports:
    return SingleToolTransports(
        vts_runtime=vts_tool.start(
            config=config,
            character_id=character_id,
            session=session,
            tts_engine=output_resources.tts_engine,
            machine=machine,
        ),
        live_runtime=live_tool.start(config=config, room_override=args.bilibili_room),
        web_search_runtime=search_web_tool.start_runtime(config, root=root),
        qq_bridge=qq_tool.create_from_cli(
            args=args,
            session=session,
            config=config,
        ),
        screen_vision_timeout=background_tool.screen_vision_timeout(config),
    )
