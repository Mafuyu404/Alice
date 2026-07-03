"""Background task action tool module."""

from kokoro.action.tools.task.manager import AgentTask, TaskManager
from kokoro.action.tools.task.spec import register


def create_manager() -> TaskManager:
    return TaskManager()


def cancel_all(manager: TaskManager, *, reason: str = "shutdown", printer=print) -> int:
    if not hasattr(manager, "cancel_all"):
        return 0
    cancelled_count = manager.cancel_all(reason)
    if cancelled_count:
        printer(f"  [agent-task] cancelled {cancelled_count} active task(s)")
    return cancelled_count


__all__ = ["AgentTask", "TaskManager", "cancel_all", "create_manager", "register"]
