"""Tool handler implementations. Each handler returns a string result for the LLM."""

from __future__ import annotations

import datetime
import logging
import time

from kokoro import prompts
from kokoro import screen_interest
from kokoro import vision

logger = logging.getLogger(__name__)


def handle_get_current_time(arguments: dict, **context) -> str:
    now = datetime.datetime.now()
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    wd = weekdays[now.weekday()]
    return f"现在是 {now.year}年{now.month}月{now.day}日 {wd} {now.hour:02d}:{now.minute:02d}:{now.second:02d}"


def handle_get_current_app(arguments: dict, **context) -> str:
    try:
        fg = vision.get_foreground_app()
    except Exception as exc:
        return f"无法获取前台窗口信息：{exc}"
    if not fg or not fg.get("title"):
        return "当前无法确定前台窗口。"
    return f"当前前台窗口：{fg['title']}（进程：{fg['process']}）"


def handle_search_memory(arguments: dict, **context) -> str:
    memory_backend = context.get("memory_backend")
    if memory_backend is None:
        return prompts.get("tool_handlers.memory_not_initialized", "记忆系统未初始化。")
    if not getattr(memory_backend, "ready", False):
        return prompts.get("tool_handlers.memory_unavailable", "记忆系统不可用。")

    query = arguments.get("query", "").strip()
    if not query:
        return prompts.get("tool_handlers.memory_empty_query", "搜索查询为空。")

    character_id = context.get("character_id", "default")
    try:
        result = memory_backend.get_context(query, user_id=character_id)
    except Exception as exc:
        logger.warning("search_memory failed: %s", exc)
        return f"搜索记忆时出错：{exc}"

    if not result or not result.strip():
        return "没有找到相关的过往记忆。"

    return result


def handle_look_at_screen(arguments: dict, **context) -> str:
    timeout = context.get("tool_timeout", 45)
    try:
        foreground = vision.get_foreground_app()
    except Exception:
        foreground = None

    if screen_interest.foreground_is_private(foreground):
        return prompts.get("tool_calling.privacy_blocked", "当前窗口可能包含隐私内容，已跳过屏幕识别。")

    focus = arguments.get("focus", "").strip()
    prompt_text = focus or prompts.get("tool_handlers.look_at_screen_default", "请详细描述当前桌面截图的内容，包括前台窗口的标题、正文、按钮和关键信息。")
    try:
        result = vision.detect_desktop(prompt=prompt_text, timeout=timeout)
    except Exception as exc:
        logger.exception("look_at_screen failed")
        return prompts.format_prompt("tool_calling.tool_error", error=f"{type(exc).__name__}: {exc}")

    if not result or not result.strip():
        return prompts.get("tool_handlers.empty_screen_content", "屏幕识别没有返回可用内容。")

    # Store screen context in session for future reference
    session = context.get("session")
    if session and hasattr(session, "add_screen_context"):
        session.add_screen_context(result.strip()[:600])

    prefix = prompts.get("tool_calling.look_at_screen_prefix", "屏幕识别结果：\n")
    return prefix + result.strip()[:2000]


def handle_save_to_memory(arguments: dict, **context) -> str:
    memory_backend = context.get("memory_backend")
    if memory_backend is None:
        return prompts.get("tool_handlers.memory_not_initialized", "记忆系统未初始化。")
    if not getattr(memory_backend, "ready", False):
        return "记忆系统不可用。"

    content = arguments.get("content", "").strip()
    if not content:
        return "要记住的内容为空。"

    importance = arguments.get("importance", "medium")
    character_id = context.get("character_id", "default")

    stored = f"[{importance}] {content}"
    try:
        memory_backend.store(stored, f"重要性: {importance}", user_id=character_id)
    except Exception as exc:
        logger.warning("save_to_memory failed: %s", exc)
        return f"保存记忆时出错：{exc}"

    return f"已记住：{content}"
