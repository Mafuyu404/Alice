"""High-level QQ input runtime for adapters."""

from __future__ import annotations

from kokoro.core import input_events
from kokoro.action.tools.qq import media as qq_media

from kokoro.action.tools.qq.environment import QQEnvironment
from kokoro.action.tools.qq.helpers import (
    _config_float,
    _config_int,
    build_raw_message_from_onebot,
)
from kokoro.action.tools.qq.input_recording import (
    recent_conversation_id,
    record_image_understanding,
    record_search_request_candidate,
    record_sent_decision,
    record_social_feedback_candidate,
)
from kokoro.action.tools.qq.models import QQRawMessage
from kokoro.action.tools.qq.participant import QQParticipationDecision


class QQInputRuntime:
    """High-level QQ input runtime for real adapters to call."""

    def __init__(
        self,
        *,
        session,
        model: str | None = None,
        config: dict | None = None,
    ) -> None:
        self.session = session
        self.config = dict(config or cfg.get("qq", {}) or {})
        self.self_id = ""
        self.environment = QQEnvironment(
            session=session,
            max_messages_per_conversation=_config_int(self.config, "max_messages_per_conversation", 200),
            packet_max_lines=_config_int(self.config, "packet_max_lines", 80),
            packet_max_age_seconds=_config_float(self.config, "packet_max_age_seconds", 180.0),
            idle_packet_max_age_seconds=_config_float(self.config, "idle_packet_max_age_seconds", 90.0),
        )
        self.image_processor = qq_media.QQImageProcessor(
            session=session,
            on_understood=self._record_image_understanding,
            section=self.config.get("image_understanding", {}) if isinstance(self.config.get("image_understanding", {}), dict) else {},
        )
        self.batch_quiet_seconds = _config_float(self.config, "batch_quiet_seconds", 4.0)
        self.batch_min_unread = _config_int(self.config, "batch_min_unread", 1)

    def ingest_onebot_event(self, event: dict) -> QQRawMessage | None:
        message = build_raw_message_from_onebot(event)
        if message is None:
            return None
        if message.self_id:
            self.self_id = message.self_id
        self.environment.ingest(message)
        self._record_social_feedback_candidate(message)
        self._record_search_request_candidate(message)
        self.image_processor.consider_message(
            message,
            recent_lines=self.environment.recent_lines(message.conversation_id, limit=20),
        )
        return message

    def ingest_message(self, message: QQRawMessage) -> None:
        if message.self_id:
            self.self_id = message.self_id
        self.environment.ingest(message)
        self._record_social_feedback_candidate(message)
        self._record_search_request_candidate(message)
        self.image_processor.consider_message(
            message,
            recent_lines=self.environment.recent_lines(message.conversation_id, limit=20),
        )

    def _record_social_feedback_candidate(self, message: QQRawMessage) -> None:
        record_social_feedback_candidate(self, message)

    def _record_search_request_candidate(self, message: QQRawMessage) -> None:
        record_search_request_candidate(self, message)

    def _record_image_understanding(self, content: str, metadata: dict) -> None:
        record_image_understanding(self, content, metadata)

    def poll(self, *, absorb_before_decide: bool = True) -> QQParticipationDecision:
        packets = self.environment.due_packets(
            min_unread=self.batch_min_unread,
            quiet_seconds=self.batch_quiet_seconds,
        )
        packets = [
            packet for packet in packets
            if not (
                packet.turn_key
                and not packet.idle_probe
                and self.environment.has_turn_response(packet.conversation_id, packet.turn_key)
            )
        ]
        for packet in packets:
            priority: input_events.InputPriority = "normal"
            if packet.message_type == "private":
                priority = "high"
            self.environment.publish_packet(packet, priority=priority)

        if packets:
            return QQParticipationDecision(reason="qq input published to inner stream")
        return QQParticipationDecision()

    def recent_conversation_id(self, *, message_type: str = "group") -> str:
        return recent_conversation_id(self, message_type=message_type)

    def record_sent(self, decision: QQParticipationDecision, *, self_id: str = "", nickname: str = "") -> None:
        record_sent_decision(self, decision, self_id=self_id, nickname=nickname)
