"""QQ input runtime autonomous participation helpers."""

from __future__ import annotations

from kokoro.core import input_events
from kokoro.action.tools.qq.helpers import _qq_decision_from_action_batch, clean_qq_reply
from kokoro.action.tools.qq.models import QQContextPacket
from kokoro.action.tools.qq.participant import QQParticipationDecision


def decide_autonomous(
    runtime,
    packets: list[QQContextPacket],
    packet_events: list[input_events.InputEvent],
) -> QQParticipationDecision:
    autonomous = getattr(runtime.session, "autonomous_step", None)
    if autonomous is None or not getattr(autonomous, "enabled", False):
        return runtime.participant.decide(packets)
    context = {}
    provider = getattr(runtime.session, "_inner_stream_event_context", None)
    if callable(provider):
        try:
            context = provider() or {}
        except Exception:
            context = {}
    try:
        from kokoro.action.tools.qq import media as qq_media

        sticker_query = " ".join(
            str(getattr(p, "content", "") or "")[:100] for p in packets[-3:]
        ) or ""
        sticker_candidates = qq_media.sticker_candidates_for_context(sticker_query, limit=20)
        if sticker_candidates:
            context["sticker_candidates"] = sticker_candidates
    except Exception:
        pass
    batch = autonomous.decide_batch(
        events=packet_events,
        context=context,
        qq_packets=packets,
        trigger_reason="QQ autonomous participation",
        capabilities=["say_qq", "send_sticker", "observe_screen", "search_web", "write_memory", "update_cognition", "observe", "wait"],
        cooldown_scope="qq",
    )
    public_decision = _qq_decision_from_action_batch(batch, runtime.session.character_name)
    background_actions = [
        action for action in batch.actions
        if action.action not in {"say_qq", "send_sticker"}
    ]
    if background_actions:
        from kokoro.action import ActionBatch

        autonomous.execute_batch(
            ActionBatch(
                actions=background_actions,
                reason=batch.reason,
                cycle_id=batch.cycle_id,
                causality_id=batch.causality_id,
            ),
            events=packet_events,
            context=context,
            trigger_reason="QQ autonomous participation",
        )
    if public_decision.action == "say_qq":
        decision = public_decision
    elif public_decision.action == "send_sticker":
        decision = public_decision
    else:
        return QQParticipationDecision(reason=batch.reason or "action_batch")
    if decision.action == "say_qq":
        cleaned = clean_qq_reply(decision.message, runtime.session.character_name)
        if not cleaned:
            return QQParticipationDecision(reason="empty autonomous message")
        if len(cleaned) > runtime.participant.max_message_chars:
            cleaned = cleaned[: runtime.participant.max_message_chars].rstrip()
        marker = getattr(autonomous, "mark_social_output", None)
        if callable(marker):
            marker(decision.conversation_id)
        return QQParticipationDecision(
            action="say",
            conversation_id=decision.conversation_id,
            message=cleaned,
            reason=decision.reason,
        )
    if decision.action == "send_sticker":
        marker = getattr(autonomous, "mark_social_output", None)
        if callable(marker):
            marker(decision.conversation_id)
        return runtime.participant.materialize_sticker_decision(QQParticipationDecision(
            action="send_sticker",
            conversation_id=decision.conversation_id,
            sticker_id=decision.sticker_id,
            message=clean_qq_reply(decision.message, runtime.session.character_name) if decision.message else "",
            reason=decision.reason,
        ), packets)
    return QQParticipationDecision(reason=decision.reason or decision.action)
