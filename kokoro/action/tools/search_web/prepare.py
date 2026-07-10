"""Prepare web search actions."""

from __future__ import annotations

import re

from kokoro.action import model as action_model
from kokoro.action import tool_spec


_PREPARE_PROMPT_PATH = __file__.replace("prepare.py", "prepare.md")

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
    llm_query = _prepare_query_with_llm(ctx, action=action, current_query=query)
    if llm_query is not None:
        query = llm_query
    elif not query:
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


def _prepare_query_with_llm(
    ctx: tool_spec.ToolContext,
    *,
    action: action_model.Action,
    current_query: str,
) -> str | None:
    chat = _llm_chat(ctx)
    if not callable(chat):
        return None
    try:
        raw = chat(
            [
                {"role": "system", "content": _read_prepare_prompt()},
                {
                    "role": "user",
                    "content": (
                        f"当前已有 query：{current_query or '(空)'}\n"
                        f"行动理由：{action.reason or '(空)'}\n"
                        f"行动参数：{action.args}\n\n"
                        "当前 inner_stream：\n"
                        f"{_inner_stream_text(ctx)[-3000:]}"
                    ),
                },
            ],
            {"function": "search_web_prepare_query", "max_tokens": 80, "timeout": 30},
        )
    except Exception:
        return None
    return _clean_query(raw)


def _llm_chat(ctx: tool_spec.ToolContext):
    runtime = getattr(ctx.session, "life_runtime", None)
    llm = getattr(runtime, "llm", None)
    return getattr(llm, "chat", None)


def _read_prepare_prompt() -> str:
    try:
        with open(_PREPARE_PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "你在为网页搜索工具提炼 query。只返回 query 文本本身；没有具体搜索问题则返回空字符串。"


def _clean_query(raw: str) -> str:
    text = re.sub(r"```(?:text|json)?\s*\n?(.*?)```", r"\1", str(raw or ""), flags=re.DOTALL).strip()
    text = text.strip().strip('"').strip("'").strip()
    text = re.sub(r"^(?:query|查询|搜索词)\s*[:：]\s*", "", text, flags=re.IGNORECASE).strip()
    if text.lower() in {"null", "none", "n/a", "(empty)", "empty"}:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = lines[0] if lines else ""
    return text[:180].strip()


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
