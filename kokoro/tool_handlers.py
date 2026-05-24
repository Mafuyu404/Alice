"""Tool handler implementations. Each handler returns a string result for the LLM."""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
from pathlib import Path
import subprocess
import threading
import time

from kokoro import memory as memory_mod
from kokoro import prompts
from kokoro import screen_interest
from kokoro import vision

logger = logging.getLogger(__name__)

# Used by handle_vts_expression to schedule async revert
_vts_revert_timer: threading.Timer | None = None
_vts_revert_lock = threading.Lock()

_VTS_EXPRESSION_ALIASES = {
    "confused": "doubt",
    "confuse": "doubt",
    "疑惑": "doubt",
    "困惑": "doubt",
    "thinking_face": "thinking",
    "think": "thinking",
    "smiling": "smile",
    "happy_smile": "happy",
}


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
        session = context.get("session")
        counterpart = getattr(session, "memory_counterpart", "") or getattr(session, "user_name", "")
        if hasattr(memory_backend, "get_context_multi"):
            result = memory_backend.get_context_multi(
                query,
                memory_mod.context_user_ids(character_id, counterpart),
            )
        else:
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
    session = context.get("session")
    counterpart = getattr(session, "memory_counterpart", "") or ""
    user_id = memory_mod.scoped_user_id(character_id, counterpart)

    stored = f"[{importance}] {content}"
    try:
        memory_backend.store(stored, f"重要性: {importance}", user_id=user_id)
    except Exception as exc:
        logger.warning("save_to_memory failed: %s", exc)
        return f"保存记忆时出错：{exc}"

    return f"已记住：{content}"


def handle_send_qq_message(arguments: dict, **context) -> str:
    message = str(arguments.get("message") or arguments.get("content") or "").strip()
    if not message:
        return "QQ 消息为空，未发送。"
    conversation_id = str(arguments.get("conversation_id") or "").strip()
    reason = str(arguments.get("reason") or "").strip() or "llm_decided"
    sender = context.get("qq_send_message")
    if not callable(sender):
        return "QQ 通道未连接，消息未发送。"
    try:
        result = sender(message, conversation_id=conversation_id, reason=reason)
    except Exception as exc:
        logger.warning("send_qq_message failed: %s", exc)
        return f"QQ 发送失败：{type(exc).__name__}: {exc}"
    return str(result or "QQ 发送未返回结果。")


def handle_vts_expression(arguments: dict, **context) -> str:
    """Set a Live2D expression via VTube Studio.

    Context required: ``vts_controller`` (VTSController instance).
    Optional: ``vts_arbiter`` (VTSExpressionArbiter), ``event_loop``.
    """
    global _vts_revert_timer
    ctrl = context.get("vts_controller")
    if ctrl is None:
        return "VTS 未连接，无法控制表情。"

    expr = _normalize_vts_expression(arguments.get("expression", ""))
    if not expr:
        return "未指定表情。"

    intensity = float(arguments.get("intensity", 1.0))
    duration = float(arguments.get("duration_seconds", 0))

    if not ctrl.has_expression(expr):
        return f"未知表情：{expr}"

    arbiter = context.get("vts_arbiter")
    params = ctrl.get_expression_params(expr, intensity)
    if arbiter is not None:
        arbiter.set_layer("tool", params)

        def _delayed_clear():
            try:
                arbiter.clear_layer("tool")
            except Exception:
                pass

        if duration > 0:
            with _vts_revert_lock:
                if _vts_revert_timer and _vts_revert_timer.is_alive():
                    _vts_revert_timer.cancel()
                _vts_revert_timer = threading.Timer(duration, _delayed_clear)
                _vts_revert_timer.daemon = True
                _vts_revert_timer.start()
        return f"已切换表情为 {expr}"

    # Schedule async injection on the main event loop
    loop: asyncio.AbstractEventLoop | None = context.get("event_loop")
    if loop is None or loop.is_closed():
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return "无法获取事件循环。"

    async def _apply():
        await ctrl.set_expression(expr, intensity)

    future = asyncio.run_coroutine_threadsafe(_apply(), loop)

    # Schedule auto-revert if duration > 0
    if duration > 0:
        revert_expr = context.get("vts_revert_expression", "neutral")

        async def _revert():
            await ctrl.set_expression(revert_expr, 1.0)

        def _delayed_revert():
            try:
                asyncio.run_coroutine_threadsafe(_revert(), loop)
            except Exception:
                pass

        with _vts_revert_lock:
            if _vts_revert_timer and _vts_revert_timer.is_alive():
                _vts_revert_timer.cancel()
            _vts_revert_timer = threading.Timer(duration, _delayed_revert)
            _vts_revert_timer.daemon = True
            _vts_revert_timer.start()

    try:
        future.result(timeout=5)
    except Exception as exc:
        return f"表情设置失败：{exc}"

    return f"已切换表情为 {expr}"


def _normalize_vts_expression(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _VTS_EXPRESSION_ALIASES.get(raw, _VTS_EXPRESSION_ALIASES.get(raw.lower(), raw))


def handle_vts_motion(arguments: dict, **context) -> str:
    body_driver = context.get("vts_body_driver")
    arbiter = context.get("vts_arbiter")
    if body_driver is None and arbiter is None:
        return "VTS 身体控制未连接。"

    motion = str(arguments.get("motion") or "idle").strip().lower()
    intensity = _clamp_float(arguments.get("intensity", 0.75), 0.0, 1.0)
    duration = _clamp_float(arguments.get("duration_seconds", 4.0), 0.5, 12.0)
    reason = str(arguments.get("reason") or motion or "vts_motion").strip()

    if body_driver is not None and hasattr(body_driver, "play_direct_motion"):
        body_driver.play_direct_motion(motion, intensity=intensity, duration=duration, reason=reason)
        return f"已执行 Live2D 动作：{motion}"

    return "VTS 身体驱动未启用，无法执行身体动作。"


def _clamp_float(value, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))


def _find_claude_code() -> str:
    """Locate the claude executable. Returns path or raises FileNotFoundError."""
    candidates = ["claude", "claude.exe"]
    for c in candidates:
        try:
            import shutil
            path = shutil.which(c)
            if path:
                return path
        except Exception:
            pass
    # Fallback: common install locations
    fallbacks = [
        os.path.expanduser("~/.npm-global/bin/claude"),
        os.path.expanduser("~/.npm/bin/claude"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "npm", "claude"),
        "/usr/local/bin/claude",
    ]
    for fb in fallbacks:
        if os.path.isfile(fb):
            return fb
    raise FileNotFoundError("Claude Code 未找到。请确保已安装 npm install -g @anthropic-ai/claude-code")


def _run_claude_code_sync(
    task: str,
    working_dir: str | None = None,
    timeout: float = 120.0,
) -> tuple[str, str | None]:
    """Run claude -p synchronously. Returns (output, error)."""
    try:
        exe = _find_claude_code()
    except FileNotFoundError as exc:
        return "", str(exc)

    env = os.environ.copy()
    if working_dir:
        cwd = working_dir
    else:
        from pathlib import Path
        cwd = str(Path(__file__).resolve().parent.parent)

    desktop = _windows_desktop_dir()
    system_prompt = prompts.format_prompt(
        "claude_code_exec.system",
        cwd=cwd,
        desktop=desktop,
    )
    user_prompt = prompts.format_prompt(
        "claude_code_exec.user",
        task=task,
    )

    cmd = [
        exe,
        "--permission-mode", "bypassPermissions",
        "--no-session-persistence",
        "--add-dir", str(desktop),
        "--append-system-prompt", system_prompt,
        "-p",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=user_prompt,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
        output = (result.stdout or "") + (result.stderr or "")
        output = output.strip()
        if result.returncode != 0:
            return output[:3000], f"Claude Code 退出码 {result.returncode}"
        if _looks_like_noop_claude_output(output):
            return output[:3000], "Claude Code 没有执行请求的任务"
        return output[:3000], None
    except subprocess.TimeoutExpired:
        return "", f"任务执行超时（{timeout}秒）"
    except FileNotFoundError:
        return "", "Claude Code 未安装或不在 PATH 中"
    except Exception as exc:
        return "", f"执行失败：{type(exc).__name__}: {exc}"


def _looks_like_noop_claude_output(output: str) -> bool:
    text = (output or "").strip().lower()
    if not text:
        return True
    noop_markers = (
        "i'm ready to operate",
        "i am ready to operate",
        "what do you need me to do",
        "ready to help",
        "我已准备好",
        "我准备好",
        "需要我做什么",
    )
    return any(marker in text for marker in noop_markers)


def _background_worker(
    task_id: str,
    task: str,
    working_dir: str | None,
    manager: object,
    timeout: float,
) -> None:
    """Run Claude Code in background thread, updating task state."""
    manager.update(task_id, status="running", progress="正在启动 Claude Code...")
    print(f"  [agent-task] {task_id} running task={task[:120]}")
    try:
        output, error = _run_claude_code_sync(task, working_dir, timeout)
        current = manager.get(task_id) if hasattr(manager, "get") else None
        if current is not None and getattr(current, "status", "") == "cancelled":
            print(f"  [agent-task] {task_id} cancelled; dropping result")
            return
        if error:
            manager.update(task_id, status="failed", error=error, progress="")
            print(f"  [agent-task] {task_id} failed error={error} output={output[:300]}")
        else:
            manager.update(task_id, status="completed", result=output, progress="已完成")
            print(f"  [agent-task] {task_id} completed result={output[:300]}")
    except Exception as exc:
        manager.update(task_id, status="failed", error=f"{type(exc).__name__}: {exc}", progress="")
        print(f"  [agent-task] {task_id} failed exception={type(exc).__name__}: {exc}")


def handle_claude_code_exec(arguments: dict, **context) -> str:
    """Execute a task via Claude Code in the background."""
    task = arguments.get("task", "").strip()
    if not task:
        return "任务描述为空，请提供要完成的目标。"

    working_dir = arguments.get("working_dir", "").strip() or None

    task_manager = context.get("task_manager")
    if task_manager is None:
        return "任务管理系统未初始化。"

    if hasattr(task_manager, "list_active"):
        active = task_manager.list_active()
        if active:
            return (
                "已有智能体任务正在执行，未启动新的重复任务。\n"
                + "\n".join(t.to_prompt_line() for t in active[:5])
            )

    task_obj = task_manager.create(description=task)
    task_manager.update(task_obj.id, status="pending", progress="队列中")

    timeout = context.get("tool_timeout", 120.0)

    thread = threading.Thread(
        target=_background_worker,
        args=(task_obj.id, task, working_dir, task_manager, timeout),
        daemon=True,
    )
    thread.start()

    # Fast failures (CLI argument/auth/permission errors) often happen before
    # the dialogue model speaks. Surface them immediately instead of returning
    # a misleading "background task created" message.
    thread.join(timeout=1.2)
    current = task_manager.get(task_obj.id)
    if current is not None and current.is_terminal:
        return current.to_result()

    return (
        f"任务已创建，ID: {task_obj.id}\n"
        f"描述：{task}\n"
        f"正在后台处理中，处理完成后我会告诉你结果。"
        f"你也可以随时问我「好了吗」来查询进度。"
    )


def _windows_desktop_dir() -> Path:
    try:
        import ctypes
        from ctypes import wintypes

        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        # CSIDL_DESKTOPDIRECTORY
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0:
            path = Path(buf.value)
            if path:
                return path
    except Exception:
        pass
    return Path.home() / "Desktop"


def handle_check_task_progress(arguments: dict, **context) -> str:
    """Query the status of one or all tasks."""
    task_id = arguments.get("task_id", "").strip()
    task_manager = context.get("task_manager")
    if task_manager is None:
        return "任务管理系统未初始化。"

    if task_id:
        task = task_manager.get(task_id)
        if task is None:
            return f"未找到任务 {task_id}。"
        return task.to_result()

    active = task_manager.list_active()
    if not active:
        if hasattr(task_manager, "list_all"):
            recent = sorted(task_manager.list_all(), key=lambda t: t.created_at, reverse=True)[:5]
            if recent:
                return "最近的智能体任务：\n" + "\n\n".join(t.to_result() for t in recent)
        return "当前没有进行中的任务。"
    return "进行中的任务：\n" + "\n".join(t.to_prompt_line() for t in active)


def handle_list_active_tasks(arguments: dict, **context) -> str:
    """List all active agent tasks."""
    task_manager = context.get("task_manager")
    if task_manager is None:
        return "任务管理系统未初始化。"

    active = task_manager.list_active()
    if not active:
        return "当前没有进行中的任务。"

    lines = [f"共有 {len(active)} 个进行中的任务："]
    for t in active:
        lines.append(t.to_prompt_line())
    return "\n".join(lines)


def handle_cancel_task(arguments: dict, **context) -> str:
    """Cancel a pending or running task."""
    task_id = arguments.get("task_id", "").strip()
    if not task_id:
        return "请提供要取消的任务ID。"

    task_manager = context.get("task_manager")
    if task_manager is None:
        return "任务管理系统未初始化。"

    task = task_manager.get(task_id)
    if task is None:
        return f"未找到任务 {task_id}。"

    if task.is_terminal:
        return f"任务 {task_id} 已经处于 {task.status} 状态，无需取消。"

    task_manager.update(task_id, status="cancelled", progress="已取消")
    return f"任务 {task_id} 已取消。"
