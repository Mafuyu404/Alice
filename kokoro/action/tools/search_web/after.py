"""Post-execution hooks for web search tools."""

from __future__ import annotations

import re

from kokoro.action import tool_spec


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
    _record_search_event(ctx, _digest_search_result(query=query, content=result.content, status=result.status), metadata)


def _record_search_event(ctx: tool_spec.ToolContext, content: str, metadata: dict) -> None:
    callback = getattr(ctx.session, "_record_inner_stream_search_event", None)
    if callable(callback):
        callback(content, "web_search", metadata)


def _digest_search_result(*, query: str, content: str, status: str) -> str:
    text = str(content or "").strip()
    if status != "success":
        return f"web search did not return usable material for query: {query}\n{text[:600]}".strip()
    titles = _extract_titles(text)
    if not titles:
        return (
            f"web search material for query: {query}\n"
            "No clear candidate titles were available. Treat this as noisy or unstructured material and decide whether to revise the query, change source, think internally, or leave a pending thread."
        )
    kept = "\n".join(f"- {title}" for title in titles[:4])
    return (
        f"web search material for query: {query}\n"
        "These candidates are material for that query only, not a new topic by themselves.\n"
        f"candidate titles:\n{kept}\n"
        "Self-review before using them: did this answer the original question, suggest a better source/query, or show that the result is noisy?"
    )


def _extract_titles(text: str) -> list[str]:
    titles: list[str] = []
    for match in re.finditer(r"^\s*\d+\.\s+title:\s*(.+)$", text, flags=re.MULTILINE):
        title = match.group(1).strip()
        if title and title not in titles:
            titles.append(title[:140])
    return titles
