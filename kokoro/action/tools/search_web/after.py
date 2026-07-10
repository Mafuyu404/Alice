"""Post-execution hooks for web search tools."""

from __future__ import annotations

import json
import re

from kokoro.action import tool_spec
from kokoro.core import lifecycle_debug


_PROMPT_PATH = __file__.replace("after.py", "after.md")


def after_search_web(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
    result: tool_spec.ToolResult,
) -> None:
    query = str(result.metadata.get("query") or prepared.args.get("query") or "").strip()
    expected_use = str(
        result.metadata.get("expected_use") or prepared.args.get("expected_use") or "action_tool"
    ).strip()
    reason = prepared.action.reason
    if result.status == "skipped" and not query:
        lifecycle_debug.log("search_web.after.skipped_empty_query", reason=reason, prepared=prepared)
        return
    if query:
        _record_search_event(
            ctx,
            f"web search intent: {query}\nreason: {reason or 'action selected web search'}",
            {
                "action": "web_search_intent",
                "query": query,
                "reason": reason,
                "expected_use": expected_use,
            },
        )
    event_action = "web_search_result" if result.status == "success" else "web_search_error"
    metadata = {
        "action": event_action,
        "query": query,
        "reason": reason,
        "expected_use": expected_use,
        "status": result.status,
    }
    if "error" in result.metadata:
        metadata["error"] = result.metadata["error"]
    raw_content = str(result.metadata.get("raw_content") or result.content or "")
    lifecycle_debug.log(
        "search_web.after.raw_result",
        query=query,
        status=result.status,
        chars=len(raw_content),
        content=raw_content,
    )
    digest = _digest_search_result(
        ctx,
        query=query,
        content=raw_content,
        status=result.status,
        reason=reason,
    )
    _record_search_event(ctx, digest, metadata)


def _record_search_event(ctx: tool_spec.ToolContext, content: str, metadata: dict) -> None:
    callback = getattr(ctx.session, "_record_inner_stream_search_event", None)
    if callable(callback):
        callback(content, "web_search", metadata)


def _digest_search_result(
    ctx: tool_spec.ToolContext,
    *,
    query: str,
    content: str,
    status: str,
    reason: str = "",
) -> str:
    text = str(content or "").strip()
    if status != "success":
        return f"web search did not return usable material for query: {query}\n{text[:600]}".strip()
    llm_digest = _llm_digest(ctx, query=query, content=text, reason=reason)
    if llm_digest:
        return llm_digest
    titles = _extract_titles(text)
    if not titles:
        return (
            f"搜索结果已返回：{query}\n"
            "工具没有得到清晰候选。把它当作噪声或不完整材料，只保留对原问题的影响。"
        )
    return (
        f"搜索结果已返回：{query}\n"
        f"工具看到了 {len(titles)} 个候选，但不会把候选标题作为新话题直接交给生命流。"
        "只继续保留它们对原本注意对象的帮助、偏差或下一步线索。"
    )


def _llm_digest(ctx: tool_spec.ToolContext, *, query: str, content: str, reason: str) -> str:
    chat = _llm_chat(ctx)
    if not callable(chat):
        return ""
    try:
        prompt = _read_after_prompt()
        raw = chat(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        f"原始意图：{reason or '(未提供)'}\n"
                        f"query：{query}\n\n"
                        "原始搜索材料：\n"
                        f"{content[:6000]}"
                    ),
                },
            ],
            {"function": "search_web_after_digest", "max_tokens": 360, "timeout": 45},
        )
    except Exception as exc:
        lifecycle_debug.log("search_web.after.digest_error", query=query, error=str(exc))
        return ""
    data = _extract_json_object(raw)
    if isinstance(data, dict):
        summary = str(data.get("digest") or data.get("summary") or "").strip()
        if summary:
            return f"搜索结果消化：{summary[:1200]}"
    text = _strip_code_fence(str(raw or "")).strip()
    if not text:
        return ""
    return f"搜索结果消化：{text[:1200]}"


def _llm_chat(ctx: tool_spec.ToolContext):
    runtime = getattr(ctx.session, "life_runtime", None)
    llm = getattr(runtime, "llm", None)
    return getattr(llm, "chat", None)


def _read_after_prompt() -> str:
    try:
        with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return (
            "你在工具内部消化网页搜索结果。只输出 JSON object，字段 digest。"
            "digest 只说明结果对原问题的影响，不保留标题列表、URL、排名或无关候选。"
        )


def _extract_json_object(text: str) -> dict | None:
    raw = _strip_code_fence(text)
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


def _strip_code_fence(text: str) -> str:
    return re.sub(r"```(?:json)?\s*\n?(.*?)```", r"\1", str(text or ""), flags=re.DOTALL).strip()


def _extract_titles(text: str) -> list[str]:
    titles: list[str] = []
    for match in re.finditer(r"^\s*\d+\.\s+title:\s*(.+)$", text, flags=re.MULTILINE):
        title = match.group(1).strip()
        if title and title not in titles:
            titles.append(title[:140])
    return titles
