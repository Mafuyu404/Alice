"""Execution for memory action tools."""

from __future__ import annotations

from kokoro.action import tool_spec
from kokoro.core import memory as memory_mod
from kokoro.core import prompts


def execute_conversation_memory(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    trigger_text = str(prepared.args.get("trigger_text") or "").strip()
    reply = str(prepared.args.get("reply") or "").strip()
    speaker_id = str(prepared.args.get("speaker_id") or "").strip()
    if not trigger_text and not reply:
        return tool_spec.ToolResult(
            content="memory skipped: empty turn",
            status="skipped",
            metadata={"memory_written": False, "speaker_id": speaker_id},
        )
    if not hasattr(ctx.session, "remember"):
        return tool_spec.ToolResult(
            content="memory system is not initialized",
            status="failed",
            metadata={"memory_written": False, "speaker_id": speaker_id},
        )
    ctx.session.remember(trigger_text, reply, async_store=True)
    return tool_spec.ToolResult(
        content="conversation memory queued",
        metadata={
            "memory_written": True,
            "trigger_chars": len(trigger_text),
            "reply_chars": len(reply),
            "speaker_id": speaker_id,
        },
    )


def execute_search_memory(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    memory_system = ctx.get("memory_system") or getattr(ctx.session, "memory_system", None)
    query = str(prepared.args.get("query") or "").strip()
    if not query:
        return tool_spec.ToolResult(
            prompts.get("tool_handlers.memory_empty_query", "memory search query is empty"),
            status="failed",
            metadata={"query": query, "memory_found": False},
        )
    if memory_system is not None:
        try:
            result_text = str(memory_system.deep_recall(query) or "").strip()
        except Exception as exc:
            return tool_spec.ToolResult(
                f"memory search failed: {exc}",
                status="failed",
                metadata={"query": query, "memory_found": False, "error": str(exc)},
            )
        if not result_text:
            return tool_spec.ToolResult(
                "no relevant memory found",
                status="skipped",
                metadata={"query": query, "memory_found": False},
            )
        return tool_spec.ToolResult(
            result_text,
            metadata={
                "query": query,
                "memory_found": True,
                "result_chars": len(result_text),
                "memory_system": "life",
            },
        )

    memory_backend = ctx.get("memory_backend")
    if memory_backend is None:
        return tool_spec.ToolResult(
            prompts.get("tool_handlers.memory_not_initialized", "memory system is not initialized"),
            status="failed",
        )
    if not getattr(memory_backend, "ready", False):
        return tool_spec.ToolResult(
            prompts.get("tool_handlers.memory_unavailable", "memory system is unavailable"),
            status="failed",
        )

    character_id = ctx.get("character_id", "default")
    try:
        session = ctx.session
        if hasattr(memory_backend, "get_context_multi"):
            result = memory_backend.get_context_multi(
                query,
                memory_mod.context_user_ids(character_id),
            )
        else:
            result = memory_backend.get_context(query, user_id=character_id)
    except Exception as exc:
        return tool_spec.ToolResult(
            f"memory search failed: {exc}",
            status="failed",
            metadata={"query": query, "memory_found": False, "error": str(exc)},
        )

    if not result or not str(result).strip():
        return tool_spec.ToolResult(
            "no relevant memory found",
            status="skipped",
            metadata={"query": query, "memory_found": False},
        )
    result_text = str(result)
    return tool_spec.ToolResult(
        result_text,
        metadata={"query": query, "memory_found": True, "result_chars": len(result_text)},
    )


def execute_save_to_memory(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    content = str(prepared.args.get("content") or "").strip()
    if not content:
        return tool_spec.ToolResult(
            "memory content is empty",
            status="failed",
            metadata={"memory_written": False},
        )

    importance = str(prepared.args.get("importance") or "medium").strip() or "medium"
    memory_system = ctx.get("memory_system") or getattr(ctx.session, "memory_system", None)
    if memory_system is not None:
        try:
            draft = prepared.metadata.get("memory_draft")
            if draft is not None and hasattr(memory_system, "write_draft"):
                record = memory_system.write_draft(draft)
            else:
                inner_stream = ""
                if hasattr(ctx.session, "inner_stream"):
                    stream = getattr(ctx.session, "inner_stream", None)
                    if hasattr(stream, "get_text"):
                        inner_stream = str(stream.get_text() or "")
                    elif hasattr(stream, "get_context"):
                        inner_stream = str(stream.get_context() or "")
                record = memory_system.remember(
                    content,
                    inner_stream=inner_stream,
                    recent_context=str(getattr(ctx.session, "summary", "") or ""),
                    event_batch=str(prepared.metadata.get("event_batch") or ""),
                    importance=importance,
                )
        except Exception as exc:
            return tool_spec.ToolResult(
                f"memory save failed: {exc}",
                status="failed",
                metadata={"memory_written": False, "error": str(exc)},
            )
        if record is None:
            return tool_spec.ToolResult(
                "memory save skipped",
                status="skipped",
                metadata={"memory_written": False, "memory_system": "life"},
            )
        return tool_spec.ToolResult(
            f"remembered: {record.summary or record.content}",
            metadata={
                "memory_written": True,
                "content_chars": len(content),
                "importance": importance,
                "memory_system": "life",
                "memory_record_id": record.id,
                "record_form": record.record_form,
            },
        )

    memory_backend = ctx.get("memory_backend")
    if memory_backend is None:
        return tool_spec.ToolResult(
            prompts.get("tool_handlers.memory_not_initialized", "memory system is not initialized"),
            status="failed",
        )
    if not getattr(memory_backend, "ready", False):
        return tool_spec.ToolResult("memory system is unavailable", status="failed")

    character_id = ctx.get("character_id", "default")
    user_id = memory_mod.scoped_user_id(character_id)

    stored = f"[{importance}] {content}"
    try:
        memory_backend.store(stored, f"importance:{importance}", user_id=user_id)
    except Exception as exc:
        return tool_spec.ToolResult(
            f"memory save failed: {exc}",
            status="failed",
            metadata={"memory_written": False, "error": str(exc), "user_id": user_id},
        )

    return tool_spec.ToolResult(
        f"remembered: {content}",
        metadata={
            "memory_written": True,
            "content_chars": len(content),
            "importance": importance,
            "user_id": user_id,
        },
    )
