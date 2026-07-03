"""Action selection policy facades.

The current implementation still delegates to the older dialogue-specific
planners. This module is the convergence point for replacing them with a
single ActionBatch-producing policy.
"""

from __future__ import annotations

from kokoro.action import dialogue_orchestrator
from kokoro.action import model as action_model
from kokoro.action import multi_chat


class SingleActionPolicy:
    """Compatibility facade for the one-on-one realtime speech policy."""

    def __init__(self, *, config: dict, session, model: str, memory_backend) -> None:
        self._delegate = dialogue_orchestrator.DialogueOrchestrator(
            config=config,
            session=session,
            model=model,
            memory_backend=memory_backend,
        )

    @property
    def delegate(self) -> dialogue_orchestrator.DialogueOrchestrator:
        return self._delegate

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def direct_say_batch(
        self,
        *,
        user_text: str,
        decision: dialogue_orchestrator.DialogueDecision,
        extra_context: str | None = None,
        max_history_messages: int | None = None,
        cancel_event=None,
        stt_refine_inline: bool = False,
        usage_label: str = "chat",
    ) -> action_model.ActionBatch:
        return action_model.ActionBatch(
            actions=[
                action_model.Action(
                    action="say",
                    reason=decision.intent or "reply to current input",
                    args={
                        "user_text": user_text,
                        "decision": decision,
                        "extra_context": extra_context or "",
                        "max_history_messages": max_history_messages,
                        "cancel_event": cancel_event,
                        "stt_refine_inline": stt_refine_inline,
                        "usage_label": usage_label,
                    },
                    mode="sync",
                    visibility="public",
                    result_policy="feed_back",
                )
            ],
            reason=decision.intent or "direct speech fast path",
        )

    def scheduled_say_batch(
        self,
        *,
        user_text: str,
        decision: dialogue_orchestrator.DialogueDecision,
        extra_context: str | None = None,
        cancel_event=None,
    ) -> action_model.ActionBatch:
        return self.direct_say_batch(
            user_text=user_text,
            decision=decision,
            extra_context=extra_context,
            cancel_event=cancel_event,
            usage_label="dialogue_scheduled",
        )

    def precomputed_say_batch(
        self,
        *,
        user_text: str,
        reply: str,
        reason: str = "precomputed speech fast path",
        cancel_event=None,
    ) -> action_model.ActionBatch:
        return action_model.ActionBatch(
            actions=[
                action_model.Action(
                    action="say_precomputed",
                    reason=reason,
                    args={
                        "user_text": user_text,
                        "reply": reply,
                        "cancel_event": cancel_event,
                    },
                    mode="sync",
                    visibility="public",
                    result_policy="feed_back",
                )
            ],
            reason=reason,
        )


class MultiActorActionPolicy:
    """Compatibility facade for multi-character action selection."""

    def __init__(self, config: multi_chat.MultiChatConfig, *, runtime_config: dict | None = None) -> None:
        self._delegate = multi_chat.MultiChatOrchestrator(config, runtime_config=runtime_config)

    @property
    def delegate(self) -> multi_chat.MultiChatOrchestrator:
        return self._delegate

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)
