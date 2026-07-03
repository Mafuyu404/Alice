"""QQ input, transport, and media tool module."""

__all__ = [
    "QQAutonomousParticipant",
    "QQBridge",
    "QQContextPacket",
    "QQEnvironment",
    "QQInputRuntime",
    "QQParticipationDecision",
    "QQRawMessage",
    "boundary_reply_for_text",
    "create",
    "create_from_cli",
    "format_packet_for_decision",
    "looks_like_message_request",
    "retire_sticker",
]


def format_packet_for_decision(packet) -> str:
    from kokoro.action.tools.qq import input

    return input._format_packet_for_decision(packet)


def retire_sticker(sticker_id: str, *, reason: str = "", actor: str = "") -> dict | None:
    from kokoro.action.tools.qq import media

    return media.retire_sticker(sticker_id, reason=reason, actor=actor)


def __getattr__(name: str):
    if name in {"QQBridge", "boundary_reply_for_text", "create", "create_from_cli", "looks_like_message_request"}:
        from kokoro.action.tools.qq import bridge

        return getattr(bridge, name)
    if name in {
        "QQAutonomousParticipant",
        "QQContextPacket",
        "QQEnvironment",
        "QQInputRuntime",
        "QQParticipationDecision",
        "QQRawMessage",
    }:
        from kokoro.action.tools.qq import input

        return getattr(input, name)
    raise AttributeError(name)
