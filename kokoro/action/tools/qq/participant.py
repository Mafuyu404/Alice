"""QQ transport poll result models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class QQParticipationDecision:
    action: str = "silence"
    conversation_id: str = ""
    message: str = ""
    reason: str = ""
    sticker_id: str = ""
    message_segments: list[dict] = field(default_factory=list)

    @property
    def payload(self):
        return self.message_segments if self.message_segments else self.message

    @classmethod
    def from_dict(cls, data: dict) -> "QQParticipationDecision":
        action = str(data.get("action", "silence") or "silence").strip().lower()
        if action not in {"silence", "say", "send_sticker", "retire_sticker"}:
            action = "silence"
        message = str(data.get("message", "") or "").strip()
        sticker_id = str(data.get("sticker_id", "") or data.get("image_id", "") or "").strip()
        if action == "say" and not message:
            action = "silence"
        return cls(
            action=action,
            conversation_id=str(data.get("conversation_id", "") or "").strip(),
            message=message,
            reason=str(data.get("reason", "") or "").strip(),
            sticker_id=sticker_id,
        )
