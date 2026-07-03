"""Execute web search actions."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.action.tools.search_web.client import WebSearchClient, format_search_result


def execute_search_web(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    query = str(prepared.args.get("query") or "").strip()
    if not query:
        return tool_spec.ToolResult(
            content="web search skipped: empty query",
            status="skipped",
            metadata={"query": query},
        )

    expected_use = str(prepared.args.get("expected_use") or "action_tool").strip()
    try:
        client = _client(ctx)
        limit = int(ctx.get("search_max_results", prepared.args.get("limit") or 5) or 5)
        max_chars = int(ctx.get("search_max_event_chars", prepared.args.get("max_chars") or 6000) or 6000)
        result = client.search(query, limit=limit)
        content = format_search_result(query, result, max_chars=max_chars)
        return tool_spec.ToolResult(
            content=content,
            metadata={"query": query, "expected_use": expected_use},
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        content = f"web search failed for: {query}\nerror: {error}"
        return tool_spec.ToolResult(
            content=content,
            status="failed",
            metadata={"query": query, "error": error, "expected_use": expected_use},
        )


def _client(ctx: tool_spec.ToolContext) -> WebSearchClient:
    existing = ctx.get("web_search_client")
    if existing is not None and hasattr(existing, "search"):
        return existing
    base_url = str(ctx.get("web_search_base_url", "http://127.0.0.1:3000"))
    timeout = float(ctx.get("web_search_timeout", ctx.get("tool_timeout", 45.0)) or 45.0)
    return WebSearchClient(base_url=base_url, timeout=timeout)
