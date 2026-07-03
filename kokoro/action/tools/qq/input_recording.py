"""QQ input runtime recording helpers."""

from __future__ import annotations

import logging

from kokoro.action.tools.qq.environment import QQConversationState
from kokoro.action.tools.qq.helpers import (
    _looks_like_search_request,
    _looks_like_social_feedback,
    _search_topic_from_recent_context,
    build_self_message,
)
from kokoro.action.tools.qq.models import QQRawMessage
from kokoro.action.tools.qq.participant import QQParticipationDecision

logger = logging.getLogger(__name__)


def record_social_feedback_candidate(runtime, message: QQRawMessage) -> None:
    text = str(message.content or "").strip()
    if not _looks_like_social_feedback(text):
        return
    record = getattr(runtime.session, "record_input_event", None)
    if not callable(record):
        return
    record(
        (
            "QQ social feedback worth remembering: "
            f"conversation={message.conversation_id}; speaker={message.nickname or message.user_id}; "
            f"content={text}"
        ),
        source="qq",
        event_type="text",
        metadata={
            "input_type": "social_feedback_candidate",
            "conversation_id": message.conversation_id,
            "speaker": message.nickname or message.user_id,
            "message_id": message.message_id,
        },
        priority="high",
        lifetime="memorize_candidate",
    )


def record_search_request_candidate(runtime, message: QQRawMessage) -> None:
    text = str(message.content or "").strip()
    if not _looks_like_search_request(text):
        return
    record = getattr(runtime.session, "record_input_event", None)
    if not callable(record):
        return
    record(
        (
            "QQ search request worth considering: "
            f"conversation={message.conversation_id}; speaker={message.nickname or message.user_id}; "
            f"content={text}; search_topic_from_context={_search_topic_from_recent_context(message, runtime.environment)}"
        ),
        source="qq",
        event_type="text",
        metadata={
            "input_type": "search_request_candidate",
            "conversation_id": message.conversation_id,
            "speaker": message.nickname or message.user_id,
            "message_id": message.message_id,
        },
        priority="high",
        lifetime="session",
    )


def record_image_understanding(runtime, content: str, metadata: dict) -> None:
    record = getattr(runtime.session, "record_input_event", None)
    if callable(record):
        record(
            content,
            source="qq_image",
            event_type="chat_environment",
            metadata={"input_type": "qq_image", **dict(metadata or {})},
            priority="normal",
            lifetime="session",
        )
    logger.info("QQ image understood: %s", str(content or "")[:300])


def recent_conversation_id(runtime, *, message_type: str = "group") -> str:
    with runtime.environment._lock:
        candidates: list[tuple[float, str]] = []
        for conversation_id, state in runtime.environment._states.items():
            if conversation_id == "private:self":
                continue
            if message_type and not conversation_id.startswith(f"{message_type}:"):
                continue
            candidates.append((state.last_message_at, conversation_id))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][1]


def record_sent_decision(
    runtime,
    decision: QQParticipationDecision,
    *,
    self_id: str = "",
    nickname: str = "",
) -> None:
    if decision.action != "say" or not decision.message:
        return
    if decision.conversation_id == "private:self":
        return
    runtime.environment.mark_sent_text(decision.conversation_id, decision.message)
    state = runtime.environment._states.get(decision.conversation_id)
    if state is not None:
        packet = state.build_packet(
            max_lines=runtime.environment.packet_max_lines,
            character_name=str(getattr(runtime.session, "character_name", "") or ""),
            self_id=runtime.self_id,
            max_age_seconds=runtime.environment.packet_max_age_seconds,
        )
        if packet is not None:
            runtime.environment.mark_turn_responded(decision.conversation_id, packet.turn_key)
    if decision.conversation_id.startswith("group:") or decision.conversation_id.startswith("private:"):
        message = build_self_message(
            conversation_id=decision.conversation_id,
            message=decision.message,
            self_id=self_id or runtime.self_id,
            nickname=nickname,
        )
        with runtime.environment._lock:
            state = runtime.environment._states.get(message.conversation_id)
            if state is None:
                state = QQConversationState(max_messages=runtime.environment.max_messages_per_conversation)
                runtime.environment._states[message.conversation_id] = state
            state.append(message, count_unread=False)
        record = getattr(runtime.session, "record_self_action", None)
        if callable(record):
            record(
                f"鎴戝湪 QQ 閲屼富鍔ㄨ浜嗭細{decision.message}",
                source="qq",
                action="send_message",
                metadata={
                    "conversation_id": decision.conversation_id,
                    "reason": decision.reason,
                },
            )
