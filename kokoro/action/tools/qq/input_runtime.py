"""High-level QQ input runtime for adapters."""

from __future__ import annotations

import logging

from kokoro.core import config as cfg
from kokoro.core import input_events
from kokoro.action.tools.qq import media as qq_media

logger = logging.getLogger(__name__)

from kokoro.action.tools.qq.environment import QQEnvironment
from kokoro.action.tools.qq.helpers import (
    _config_float,
    _config_int,
    build_raw_message_from_onebot,
)
from kokoro.action.tools.qq.input_autonomous import decide_autonomous
from kokoro.action.tools.qq.input_recording import (
    recent_conversation_id,
    record_image_understanding,
    record_search_request_candidate,
    record_sent_decision,
    record_social_feedback_candidate,
)
from kokoro.action.tools.qq.models import QQRawMessage
from kokoro.action.tools.qq.participant import QQAutonomousParticipant, QQParticipationDecision


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
        participation_model = (
            model
            or str(self.config.get("participation_model", "") or "").strip()
            or cfg.dialogue_model()
            or cfg.llm_model()
        )
        self.participant = QQAutonomousParticipant(
            session=session,
            model=participation_model,
            cooldown_seconds=_config_float(self.config, "participation_cooldown_seconds", 45.0),
            max_message_chars=_config_int(self.config, "max_message_chars", 260),
            environment=self.environment,
        )
        self.autonomous_enabled = bool(self.config.get("autonomous_participation_enabled", True))
        self.batch_quiet_seconds = _config_float(self.config, "batch_quiet_seconds", 4.0)
        self.batch_min_unread = _config_int(self.config, "batch_min_unread", 1)
        self.idle_participation_seconds = _config_float(self.config, "idle_participation_seconds", 30.0)
        self.absorb_before_decide = bool(self.config.get("absorb_before_decide", False))

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
        if not packets and self.autonomous_enabled:
            idle_packet = self.environment.idle_packet(min_interval=self.idle_participation_seconds)
            if idle_packet is not None:
                packets = [idle_packet]
        packets = [
            packet for packet in packets
            if not (
                packet.turn_key
                and not packet.idle_probe
                and self.environment.has_turn_response(packet.conversation_id, packet.turn_key)
            )
        ]
        packet_events: list[input_events.InputEvent] = []
        for packet in packets:
            priority: input_events.InputPriority = "normal"
            if packet.message_type == "private":
                priority = "high"
            event = self.environment.publish_packet(packet, priority=priority)
            if event is not None:
                packet_events.append(event)

        if not self.autonomous_enabled or not packets:
            return QQParticipationDecision()

        should_absorb_now = absorb_before_decide and self.absorb_before_decide
        if should_absorb_now and packet_events:
            loop = getattr(self.session, "inner_stream_loop", None)
            if loop is not None and hasattr(loop, "evaluate_now"):
                loop.evaluate_now(packet_events, trigger_reason="QQ environment before participation")

        decision = self._decide_autonomous(packets, packet_events)
        if decision.action not in {"say", "send_sticker"}:
            return decision
        chosen_packet = next((p for p in packets if p.conversation_id == decision.conversation_id), None)
        if (
            chosen_packet is not None
            and chosen_packet.turn_key
            and not chosen_packet.idle_probe
            and self.environment.has_turn_response(chosen_packet.conversation_id, chosen_packet.turn_key)
        ):
            return QQParticipationDecision(
                action="silence",
                reason="turn already responded",
            )
        if self.environment.last_sent_age(decision.conversation_id) < self.participant.cooldown_seconds:
            return QQParticipationDecision(
                action="silence",
                reason="cooldown boundary",
            )
        duplicate_key = decision.message or decision.sticker_id
        if self.environment.has_recent_duplicate_send(decision.conversation_id, duplicate_key):
            return QQParticipationDecision(
                action="silence",
                reason="duplicate recent self message",
            )
        if chosen_packet is not None:
            self.environment.mark_sent_text(decision.conversation_id, duplicate_key)
            self.environment.mark_turn_responded(decision.conversation_id, chosen_packet.turn_key)
        return decision

    def _decide_autonomous(
        self,
        packets,
        packet_events: list[input_events.InputEvent],
    ) -> QQParticipationDecision:
        return decide_autonomous(self, packets, packet_events)

    def recent_conversation_id(self, *, message_type: str = "group") -> str:
        return recent_conversation_id(self, message_type=message_type)

    def record_sent(self, decision: QQParticipationDecision, *, self_id: str = "", nickname: str = "") -> None:
        record_sent_decision(self, decision, self_id=self_id, nickname=nickname)
