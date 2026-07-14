"""Preparation stage for QQ message sending."""

from __future__ import annotations

import json
import re
import time

from kokoro.action import model as action_model
from kokoro.action import tool_spec
from kokoro.core import lifecycle_debug


def prepare_message(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    started = time.monotonic()
    internal_args = {
        key: value
        for key, value in dict(action.args).items()
        if str(key).startswith("_")
    }
    args = {
        key: value
        for key, value in dict(action.args).items()
        if not str(key).startswith("_")
    }
    message = str(args.get("message") or args.get("content") or args.get("text") or "").strip()
    requested_conversation_id = str(
        args.get("conversation_id")
        or args.get("target")
        or args.get("channel")
        or ""
    ).strip()
    recent_conversation_id = str(ctx.get("recent_qq_conversation_id", "") or "").strip()
    conversation_id = requested_conversation_id or recent_conversation_id
    lifecycle_debug.log(
        "send_qq_message.prepare.start",
        message_chars=len(message),
        requested_conversation_id=requested_conversation_id,
        recent_conversation_id=recent_conversation_id,
    )
    if recent_conversation_id and requested_conversation_id and requested_conversation_id != recent_conversation_id:
        lifecycle_debug.log(
            "send_qq_message.prepare.conversation_id_corrected",
            requested_conversation_id=requested_conversation_id,
            recent_conversation_id=recent_conversation_id,
        )
        conversation_id = recent_conversation_id
    reason = str(args.get("reason") or action.reason or "llm_decided").strip()
    audit = _local_boundary_check(message=message)
    if not audit.get("blocked") and _needs_llm_audit(message):
        lifecycle_debug.log(
            "send_qq_message.prepare.audit_needed",
            message_chars=len(message),
            reason=reason,
        )
        audit = _audit_message(ctx, message=message, reason=reason)
    elif not audit.get("blocked"):
        lifecycle_debug.log(
            "send_qq_message.prepare.fast_allowed",
            message_chars=len(message),
            reason=audit.get("reason"),
        )
    if audit.get("blocked"):
        lifecycle_debug.log(
            "send_qq_message.prepare.audit_blocked",
            message=message,
            reason=reason,
            audit=audit,
        )
        message = ""
        reason = f"blocked_by_send_audit: {audit.get('reason') or 'message did not match current scene'}"
    args.update(
        {
            "message": message,
            "conversation_id": conversation_id,
            "reason": reason,
        }
    )
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=reason,
        metadata={
            "message_chars": len(message),
            "conversation_id": conversation_id,
            "audit": audit,
            "blocked": bool(audit.get("blocked")),
            "internal": internal_args,
            "prepare_elapsed_seconds": round(time.monotonic() - started, 3),
        },
    )


def _local_boundary_check(*, message: str) -> dict:
    text = str(message or "").strip()
    if not text:
        return {"blocked": True, "block_type": "empty", "reason": "empty message"}
    if _looks_like_generator_payload(text):
        return {
            "blocked": True,
            "block_type": "assistant_style",
            "reason": "message looks like generator/tool payload, not chat text",
        }
    return {"blocked": False, "block_type": "none", "reason": "ordinary chat text"}


def _looks_like_generator_payload(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    lower = stripped.lower()
    if stripped.startswith("```") or stripped.endswith("```"):
        return True
    if re.fullmatch(r"\{[\s\S]*\}", stripped):
        try:
            data = json.loads(stripped)
        except Exception:
            data = None
        if isinstance(data, dict) and any(key in data for key in ("action", "tool", "args", "arguments")):
            return True
    control_markers = (
        "send_qq_message",
        '"action"',
        '"tool"',
        '"args"',
        "tool_call",
        "function_call",
        "作为ai助手",
        "作为 ai 助手",
    )
    return any(marker in lower for marker in control_markers)


def _needs_llm_audit(message: str) -> bool:
    text = str(message or "").strip()
    if len(text) > 800:
        return True
    risk_markers = (
        "根据搜索",
        "搜索结果",
        "工具返回",
        "系统提示",
        "日志显示",
        "后台显示",
    )
    return any(marker in text for marker in risk_markers)


def _audit_message(ctx: tool_spec.ToolContext, *, message: str, reason: str) -> dict:
    text = str(message or "").strip()
    if not text:
        return {"blocked": True, "reason": "empty message"}
    recent_scene = str(ctx.get("recent_qq_event_batch") or "").strip()
    if not recent_scene:
        return {"blocked": False, "reason": "no recent QQ scene to audit against"}
    chat = _llm_chat(ctx)
    if not callable(chat):
        return {"blocked": False, "reason": "audit llm unavailable"}
    inner_stream = _inner_stream_text(ctx)
    recent_digest = _recent_digest_text(ctx)
    prompt = (
        "你在 send_qq_message 工具准备阶段做事实自洽检查。\n"
        "这不是重新思考人格，也不是替角色决定是否该说话；只检查即将发送的文本是否越过事实边界。\n"
        "必须允许角色口吻、亲近关系里的淘气、撒娇、反问、轻微调侃、半成品表达、承认没想好、短句和不完整念头；这些不是事实越界。\n"
        "不要判断礼貌、聊天节奏、是否应该回应、是否足够有价值、是否符合你认为的角色性格；这些都由生命流已经决定。\n"
        "日常寒暄、问今天忙什么、问最近怎么样、追问对方刚说的事、轻微调侃、半成品表达和关系表达都不是事实越界。\n"
        "不要把对话内的夸张、反问、昵称调侃或关系玩笑当成外部事实声明；它们通常是在接现场语气，不需要按字面证明成长期事实。\n"
        "只有明确硬边界才阻止：发到错误对象；编造最近现场没有出现过的人、群、消息或事件；把外部搜索/研究结果说成已经发生；用助手建议口吻代替角色本人说话。\n"
        "如果准备发送的文本包含给生成器看的写作说明、语气说明、效果说明或行动说明，而不是角色会直接发给对方的话，这属于 assistant_style。\n"
        "只输出 JSON object：{\"allow\":true|false,\"block_type\":\"none|wrong_target|fabricated_scene_fact|unsupported_external_fact|assistant_style\",\"reason\":\"简短原因\",\"message\":\"可选的更贴近现场的自然消息\"}。\n"
        "只有 block_type 是 wrong_target、fabricated_scene_fact、unsupported_external_fact 或 assistant_style 时才能 allow=false；其他情况 block_type 必须是 none。\n"
        "如果不能确定，倾向于允许并在 reason 里说明不确定点。"
    )
    user = (
        f"最近QQ现场：\n{recent_scene[-3000:]}\n\n"
        f"当前 inner_stream：\n{inner_stream[-1600:] or '(empty)'}\n\n"
        f"最近连续经历：\n{recent_digest[-1600:] or '(empty)'}\n\n"
        f"行动原因：{reason}\n"
        f"准备发送：{text}"
    )
    try:
        raw = chat(
            [{"role": "system", "content": prompt}, {"role": "user", "content": user}],
            {
                "function": "send_qq_message_audit",
                "max_tokens": 120,
                "timeout": 5,
                "priority": 0,
                "bypass_priority_queue": True,
            },
        )
    except Exception as exc:
        lifecycle_debug.log("send_qq_message.prepare.audit_error", error=str(exc))
        return {"blocked": False, "reason": "audit error"}
    data = _extract_json_object(raw)
    if not isinstance(data, dict):
        lifecycle_debug.log("send_qq_message.prepare.audit_parse_failed", raw=raw)
        return {"blocked": False, "reason": "audit parse failed"}
    allow = bool(data.get("allow"))
    block_type = str(data.get("block_type") or "none").strip()
    audit_reason = str(data.get("reason") or "").strip()
    replacement = str(data.get("message") or "").strip()
    blocking_types = {
        "wrong_target",
        "fabricated_scene_fact",
        "unsupported_external_fact",
        "assistant_style",
    }
    if allow or block_type not in blocking_types:
        lifecycle_debug.log(
            "send_qq_message.prepare.audit_allowed",
            allow=allow,
            block_type=block_type,
            reason=audit_reason,
            replacement=replacement,
        )
        return {
            "blocked": False,
            "block_type": block_type or "none",
            "reason": audit_reason or ("allowed" if allow else "not a blocking audit type"),
        }
    return {
        "blocked": True,
        "block_type": block_type,
        "reason": audit_reason or "message did not match current QQ scene",
        "replacement": replacement,
    }


def _llm_chat(ctx: tool_spec.ToolContext):
    runtime = getattr(ctx.session, "life_runtime", None)
    llm = getattr(runtime, "llm", None)
    return getattr(llm, "chat", None)


def _inner_stream_text(ctx: tool_spec.ToolContext) -> str:
    stream = getattr(ctx.session, "inner_stream", None)
    if stream is None:
        return ""
    read = getattr(stream, "read", None)
    if callable(read):
        try:
            return str(read() or "")
        except Exception:
            return ""
    return ""


def _recent_digest_text(ctx: tool_spec.ToolContext) -> str:
    runtime = getattr(ctx.session, "life_runtime", None)
    compactor = getattr(runtime, "compactor", None)
    recent_digest = getattr(compactor, "recent_digest", None)
    if callable(recent_digest):
        try:
            return str(recent_digest() or "")
        except Exception:
            return ""
    return ""


def _extract_json_object(text: str) -> dict | None:
    raw = re.sub(r"```(?:json)?\s*\n?(.*?)```", r"\1", str(text or ""), flags=re.DOTALL).strip()
    try:
        data = json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except Exception:
            return None
    return data if isinstance(data, dict) else None
