"""Execution for background agent task tools."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading

from kokoro.action import tool_spec
from kokoro.core import prompts


def execute_claude_code(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    task = str(prepared.args.get("task") or "").strip()
    working_dir = str(prepared.args.get("working_dir") or "").strip() or None
    metadata = {"task_chars": len(task), "working_dir": working_dir or "", "task_started": False}
    if not task:
        return tool_spec.ToolResult("task description is empty", status="failed", metadata=metadata)

    task_manager = ctx.get("task_manager")
    if task_manager is None:
        return tool_spec.ToolResult("task manager is not initialized", status="failed", metadata=metadata)

    if hasattr(task_manager, "list_active"):
        active = task_manager.list_active()
        if active:
            lines = "\n".join(t.to_prompt_line() for t in active[:5])
            return tool_spec.ToolResult(
                "another background task is already running; new task was not started\n" + lines,
                status="skipped",
                metadata={**metadata, "active_count": len(active)},
            )

    task_obj = task_manager.create(description=task)
    task_manager.update(task_obj.id, status="pending", progress="queued")
    timeout = float(ctx.get("tool_timeout", 120.0) or 120.0)

    thread = threading.Thread(
        target=_background_worker,
        args=(task_obj.id, task, working_dir, task_manager, timeout),
        daemon=True,
    )
    thread.start()

    thread.join(timeout=1.2)
    current = task_manager.get(task_obj.id)
    if current is not None and current.is_terminal:
        return tool_spec.ToolResult(
            current.to_result(),
            status=_status_from_task(current),
            metadata={**metadata, "task_started": True, "task_id": task_obj.id, "task_status": current.status},
        )

    return tool_spec.ToolResult(
        f"background task created: {task_obj.id}\ndescription: {task}",
        metadata={**metadata, "task_started": True, "task_id": task_obj.id, "task_status": "pending"},
    )


def execute_check_progress(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    task_id = str(prepared.args.get("task_id") or "").strip()
    task_manager = ctx.get("task_manager")
    if task_manager is None:
        return tool_spec.ToolResult("task manager is not initialized", status="failed", metadata={"task_id": task_id})

    if task_id:
        task = task_manager.get(task_id)
        if task is None:
            return tool_spec.ToolResult(
                f"task not found: {task_id}",
                status="failed",
                metadata={"task_id": task_id, "task_found": False},
            )
        return tool_spec.ToolResult(
            task.to_result(),
            status=_status_from_task(task),
            metadata={"task_id": task_id, "task_found": True, "task_status": task.status},
        )

    active = task_manager.list_active()
    if not active:
        if hasattr(task_manager, "list_all"):
            recent = sorted(task_manager.list_all(), key=lambda t: t.created_at, reverse=True)[:5]
            if recent:
                return tool_spec.ToolResult(
                    "recent background tasks:\n" + "\n\n".join(t.to_result() for t in recent),
                    metadata={"active_count": 0, "recent_count": len(recent)},
                )
        return tool_spec.ToolResult(
            "no active background tasks",
            status="skipped",
            metadata={"active_count": 0},
        )
    return tool_spec.ToolResult(
        "active background tasks:\n" + "\n".join(t.to_prompt_line() for t in active),
        metadata={"active_count": len(active)},
    )


def execute_list_active(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    task_manager = ctx.get("task_manager")
    if task_manager is None:
        return tool_spec.ToolResult("task manager is not initialized", status="failed")

    active = task_manager.list_active()
    if not active:
        return tool_spec.ToolResult("no active background tasks", status="skipped", metadata={"active_count": 0})

    lines = [f"{len(active)} active background task(s):"]
    for task in active:
        lines.append(task.to_prompt_line())
    return tool_spec.ToolResult("\n".join(lines), metadata={"active_count": len(active)})


def execute_cancel_task(
    ctx: tool_spec.ToolContext,
    prepared: tool_spec.PreparedAction,
) -> tool_spec.ToolResult:
    task_id = str(prepared.args.get("task_id") or "").strip()
    if not task_id:
        return tool_spec.ToolResult("task_id is required", status="failed", metadata={"task_id": task_id})

    task_manager = ctx.get("task_manager")
    if task_manager is None:
        return tool_spec.ToolResult("task manager is not initialized", status="failed", metadata={"task_id": task_id})

    task = task_manager.get(task_id)
    if task is None:
        return tool_spec.ToolResult(
            f"task not found: {task_id}",
            status="failed",
            metadata={"task_id": task_id, "task_found": False},
        )

    if task.is_terminal:
        return tool_spec.ToolResult(
            f"task {task_id} is already {task.status}; no cancellation needed",
            status="skipped",
            metadata={"task_id": task_id, "task_found": True, "task_status": task.status, "cancelled": False},
        )

    task_manager.update(task_id, status="cancelled", progress="cancelled")
    return tool_spec.ToolResult(
        f"task {task_id} cancelled",
        metadata={"task_id": task_id, "task_found": True, "task_status": "cancelled", "cancelled": True},
    )


def _status_from_task(task: object) -> str:
    status = str(getattr(task, "status", "") or "")
    if status == "failed":
        return "failed"
    if status == "cancelled":
        return "cancelled"
    return "success"


def _find_claude_code() -> str:
    candidates = ["claude", "claude.exe"]
    for candidate in candidates:
        try:
            import shutil

            path = shutil.which(candidate)
            if path:
                return path
        except Exception:
            pass
    fallbacks = [
        os.path.expanduser("~/.npm-global/bin/claude"),
        os.path.expanduser("~/.npm/bin/claude"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "npm", "claude"),
        "/usr/local/bin/claude",
    ]
    for fallback in fallbacks:
        if os.path.isfile(fallback):
            return fallback
    raise FileNotFoundError("Claude Code was not found. Install @anthropic-ai/claude-code or add claude to PATH.")


def _run_claude_code_sync(
    task: str,
    working_dir: str | None = None,
    timeout: float = 120.0,
) -> tuple[str, str | None]:
    try:
        exe = _find_claude_code()
    except FileNotFoundError as exc:
        return "", str(exc)

    env = os.environ.copy()
    cwd = working_dir or str(Path(__file__).resolve().parents[4])
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
        "--permission-mode",
        "bypassPermissions",
        "--no-session-persistence",
        "--add-dir",
        str(desktop),
        "--append-system-prompt",
        system_prompt,
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
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode != 0:
            return output[:3000], f"Claude Code exited with code {result.returncode}"
        if _looks_like_noop_claude_output(output):
            return output[:3000], "Claude Code did not perform the requested task"
        return output[:3000], None
    except subprocess.TimeoutExpired:
        return "", f"task timed out after {timeout} seconds"
    except FileNotFoundError:
        return "", "Claude Code is not installed or not in PATH"
    except Exception as exc:
        return "", f"execution failed: {type(exc).__name__}: {exc}"


def _looks_like_noop_claude_output(output: str) -> bool:
    text = (output or "").strip().lower()
    if not text:
        return True
    noop_markers = (
        "i'm ready to operate",
        "i am ready to operate",
        "what do you need me to do",
        "ready to help",
    )
    return any(marker in text for marker in noop_markers)


def _background_worker(
    task_id: str,
    task: str,
    working_dir: str | None,
    manager: object,
    timeout: float,
) -> None:
    manager.update(task_id, status="running", progress="starting Claude Code")
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
            manager.update(task_id, status="completed", result=output, progress="completed")
            print(f"  [agent-task] {task_id} completed result={output[:300]}")
    except Exception as exc:
        manager.update(task_id, status="failed", error=f"{type(exc).__name__}: {exc}", progress="")
        print(f"  [agent-task] {task_id} failed exception={type(exc).__name__}: {exc}")


def _windows_desktop_dir() -> Path:
    try:
        import ctypes
        from ctypes import wintypes

        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0:
            path = Path(buf.value)
            if path:
                return path
    except Exception:
        pass
    return Path.home() / "Desktop"
