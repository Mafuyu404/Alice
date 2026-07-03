"""Compatibility facade for QQ input helpers.

Implementation lives in focused modules so the QQ tool keeps clear ownership
boundaries while existing imports from ``kokoro.action.tools.qq.input`` keep
working.
"""

from kokoro.action.tools.qq.environment import QQConversationState, QQEnvironment
from kokoro.action.tools.qq.helpers import (
    _format_packet_for_decision,
    build_raw_message_from_onebot,
    build_self_message,
    clean_qq_reply,
)
from kokoro.action.tools.qq.input_runtime import QQInputRuntime
from kokoro.action.tools.qq.models import QQContextPacket, QQRawMessage
from kokoro.action.tools.qq.participant import QQAutonomousParticipant, QQParticipationDecision

__all__ = [
    "QQAutonomousParticipant",
    "QQContextPacket",
    "QQConversationState",
    "QQEnvironment",
    "QQInputRuntime",
    "QQParticipationDecision",
    "QQRawMessage",
    "build_raw_message_from_onebot",
    "build_self_message",
    "clean_qq_reply",
    "_format_packet_for_decision",
]
