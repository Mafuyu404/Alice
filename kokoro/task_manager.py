"""Agent task state management.

Tracks ongoing external agent tasks with state transitions,
progress updates, and result storage. Thread-safe.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class AgentTask:
    """A single agent task with lifecycle state."""
    id: str
    description: str
    status: str = "pending"  # pending | running | completed | failed | cancelled
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    progress: str = ""
    result: str = ""
    error: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in ("completed", "failed", "cancelled")

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    def to_prompt_line(self) -> str:
        age = self.age_seconds
        if self.is_terminal:
            return f"- [{self.id}] {self.status} ({age:.0f}s)，{self.description[:60]}"
        return f"- [{self.id}] {self.status} {self.progress or '...'} ({age:.0f}s)，{self.description[:60]}"

    def to_result(self) -> str:
        lines = [f"任务 {self.id}：{self.status}"]
        lines.append(f"描述：{self.description}")
        if self.progress:
            lines.append(f"进度：{self.progress}")
        if self.result:
            lines.append(f"结果：{self.result}")
        if self.error:
            lines.append(f"错误：{self.error}")
        if self.completed_at:
            elapsed = self.completed_at - self.created_at
            lines.append(f"耗时：{elapsed:.1f}秒")
        return "\n".join(lines)


class TaskManager:
    """Thread-safe task lifecycle manager."""

    def __init__(self):
        self._tasks: dict[str, AgentTask] = {}
        self._lock = threading.Lock()

    def create(self, description: str) -> AgentTask:
        task = AgentTask(
            id=uuid.uuid4().hex[:8],
            description=description.strip() or "(无描述)",
        )
        with self._lock:
            self._tasks[task.id] = task
        return task

    def update(self, task_id: str, **kwargs) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            for key, value in kwargs.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            if kwargs.get("status") in ("completed", "failed", "cancelled"):
                task.completed_at = time.time()

    def get(self, task_id: str) -> AgentTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_active(self) -> list[AgentTask]:
        with self._lock:
            return [
                t for t in self._tasks.values()
                if t.status in ("pending", "running")
            ]

    def list_all(self) -> list[AgentTask]:
        with self._lock:
            return list(self._tasks.values())

    def cleanup(self, max_age: float = 3600) -> int:
        """Remove terminal tasks older than max_age seconds. Returns count removed."""
        now = time.time()
        removed = 0
        with self._lock:
            keep: dict[str, AgentTask] = {}
            for tid, task in self._tasks.items():
                if task.is_terminal and task.completed_at and (now - task.completed_at) > max_age:
                    removed += 1
                else:
                    keep[tid] = task
            self._tasks = keep
        return removed
