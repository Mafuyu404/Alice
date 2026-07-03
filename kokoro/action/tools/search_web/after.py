"""Post-execution hooks for web search tools."""

from __future__ import annotations

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
    _record_search_event(ctx, result.content, metadata)


def _record_search_event(ctx: tool_spec.ToolContext, content: str, metadata: dict) -> None:
    callback = getattr(ctx.session, "_record_inner_stream_search_event", None)
    if callable(callback):
        callback(content, "web_search", metadata)
