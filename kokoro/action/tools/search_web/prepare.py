"""Prepare web search actions."""

from __future__ import annotations

import re

from kokoro.action import model as action_model
from kokoro.action import tool_spec


_ANCHOR_STOPWORDS = {
    "json",
    "http",
    "https",
    "inner_stream",
    "action_plan",
    "search_web",
    "look_at_screen",
    "observe_screen",
    "pending_threads",
    "source",
    "metadata",
}


def prepare_query(ctx: tool_spec.ToolContext, action: action_model.Action) -> tool_spec.PreparedAction:
    args = dict(action.args)
    query = str(
        args.get("query")
        or args.get("topic")
        or args.get("question")
        or args.get("intent")
        or ""
    ).strip()
    if not query:
        query = action.reason.strip()
    anchored_query = _preserve_subject_anchor(ctx, query)
    anchor_added = anchored_query != query
    query = anchored_query
    args["query"] = query
    return tool_spec.PreparedAction(
        action=action,
        args=args,
        reason=action.reason or "prepare web search query",
        metadata={"prepared_query": query, "anchor_added": anchor_added},
    )


def _preserve_subject_anchor(ctx: tool_spec.ToolContext, query: str) -> str:
    text = str(query or "").strip()
    if not text or _latin_terms(text):
        return text
    context = _inner_stream_text(ctx)
    for term in _latin_terms(context):
        if term.lower() not in text.lower():
            return f"{term} {text}"
    return text


def _inner_stream_text(ctx: tool_spec.ToolContext) -> str:
    stream = getattr(ctx.session, "inner_stream", None)
    for method in ("get_context", "get_text"):
        fn = getattr(stream, method, None)
        if callable(fn):
            try:
                return str(fn() or "")
            except Exception:
                return ""
    return str(getattr(stream, "text", "") or "")


def _latin_terms(text: str) -> list[str]:
    terms: list[str] = []
    for match in re.finditer(r"\b[A-Za-z][A-Za-z0-9_.:-]{2,}\b", str(text or "")):
        term = match.group(0).strip("._:-")
        if not term or term.lower() in _ANCHOR_STOPWORDS:
            continue
        if term not in terms:
            terms.append(term)
    return terms[:12]
