"""QQ attention and relation signal extraction."""

from __future__ import annotations

import re

from kokoro.action.tools.qq.models import QQRawMessage
from kokoro.action.tools.qq.social_identity import (
    _content_is_only_name_call,
    _extract_nonself_target,
    _is_self_message,
    _matched_self_alias,
    _participant_names,
    _reply_targets_self,
    _self_aliases,
)
from kokoro.action.tools.qq.social_guards import _looks_like_technical_advice


def _detect_attention_lines(
    messages: list[QQRawMessage],
    *,
    character_name: str = "",
    self_id: str = "",
) -> list[str]:
    signals: list[str] = []
    aliases = _self_aliases(character_name)
    self_id = str(self_id or "").strip()
    recent = messages[-12:]
    for msg in recent:
        if _is_self_message(msg, self_id):
            continue
        content = str(msg.content or "").strip()
        speaker = msg.nickname or msg.user_id or "有人"
        if not content:
            continue
        reasons: list[str] = []
        matched_alias = _matched_self_alias(content, aliases)
        if matched_alias:
            if _content_is_only_name_call(content, aliases):
                reasons.append(f"只写了角色称呼：{matched_alias}")
            else:
                reasons.append(f"提到角色称呼：{matched_alias}")
        if self_id and f"[CQ:at,qq={self_id}]" in content:
            reasons.append("@了她")
        has_name = bool(matched_alias)
        has_self_at = bool(self_id and f"[CQ:at,qq={self_id}]" in content)
        has_selfish_pronoun = bool(matched_alias)
        if msg.message_type == "private" and re.search(r"[?？]|怎么|什么|为啥|为什么|吗|呢", content):
            reasons.append("私聊里像是在抛问题")
        elif (has_name or has_self_at or has_selfish_pronoun) and re.search(r"[?？]|怎么|什么|为啥|为什么|吗|呢", content):
            reasons.append("点到她的问题")
        if (has_name or has_self_at or has_selfish_pronoun or msg.message_type == "private") and (matched_alias or "你" in content) and len(content) <= 40:
            reasons.append("短句像是在向她搭话")
        if reasons:
            unique = ",".join(dict.fromkeys(reasons))
            signals.append(f"{speaker}：{unique} -> {content[:60]}")
    if len(recent) >= 2 and not signals:
        last = recent[-1]
        content = str(last.content or "").strip()
        if content and len(content) <= 30:
            speaker = last.nickname or last.user_id or "有人"
            signals.append(f"{speaker}发了短句，可能是在接当前群聊节奏 -> {content[:60]}")
    return signals


def _detect_relation_lines(
    messages: list[QQRawMessage],
    *,
    character_name: str = "",
    self_id: str = "",
) -> list[str]:
    relations: list[str] = []
    aliases = _self_aliases(character_name)
    participant_names = _participant_names(messages, self_id)
    self_id = str(self_id or "").strip()
    recent = messages[-16:]
    for idx, msg in enumerate(recent[-8:], start=max(0, len(recent) - 8)):
        if _is_self_message(msg, self_id):
            continue
        content = str(msg.content or "").strip()
        if not content:
            continue
        speaker = msg.nickname or msg.user_id or "有人"
        direct_reasons: list[str] = []
        matched_alias = _matched_self_alias(content, aliases)
        if matched_alias:
            if _content_is_only_name_call(content, aliases):
                direct_reasons.append(f"仅称呼角色:{matched_alias}")
            else:
                direct_reasons.append(f"提到角色:{matched_alias}")
        if self_id and f"[CQ:at,qq={self_id}]" in content:
            direct_reasons.append("@本角色")
        if _reply_targets_self(content, self_id):
            direct_reasons.append("回复本角色")
        if direct_reasons:
            relations.append(f"指向本角色：{speaker}（{','.join(dict.fromkeys(direct_reasons))}） -> {content[:70]}")
            continue

        target = _extract_nonself_target(content, self_id, participants=participant_names, speaker=speaker, self_aliases=aliases)
        if target:
            relations.append(f"指向其他成员：{speaker} 可能在称呼或回应 {target} -> {content[:70]}")
            continue

        if _looks_like_technical_advice(content):
            relations.append(f"疑似技术建议：{speaker} 的话可能是在给群友建议，不一定是本角色要执行的任务 -> {content[:70]}")
            continue

        if idx >= len(recent) - 3:
            if msg.message_type == "private":
                relations.append(f"私聊现场：{speaker} 在私聊中说话 -> {content[:70]}")
            else:
                relations.append(f"群聊现场：{speaker} 参与当前群聊流动 -> {content[:70]}")

    return relations[-8:]
