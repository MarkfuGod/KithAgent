"""
Agent Base — the 'Thread' abstraction of AgentOS.

Every agent task is a 'thread' that gets scheduled by the Scheduler.
Just as OS threads have:
  - a thread ID
  - a priority
  - a state (runnable, running, blocked, terminated)
  - a stack (context)

Agent threads have:
  - a task ID
  - a priority level
  - a state
  - a context (memory snapshot + instructions)
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"      # waiting on I/O (LLM call, file read)
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentTask:
    """A unit of work — analogous to a thread/task in an OS.

    SubAgent pattern: when parent_task_id is set, this task was spawned by
    another agent (fan-out). The parent agent can await all children via
    scheduler.fan_out().
    """
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    priority: int = 1           # 0=high, 1=normal, 2=low
    state: AgentState = AgentState.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    input_data: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    caller: str = ""            # which external agent requested this
    parent_task_id: str | None = None
    children_ids: list[str] = field(default_factory=list)

    def elapsed(self) -> float | None:
        if self.started_at:
            end = self.completed_at or time.time()
            return end - self.started_at
        return None


class BaseAgent(ABC):
    """
    Abstract base for all agent 'threads'.

    Subclass this to create specialized agents:
      - FileSearchAgent: searches indexed files
      - SummaryAgent: summarizes documents
      - ProfileAgent: maintains user profile
      - CodeAnalysisAgent: analyzes code structure
    """

    name: str = "base_agent"

    @abstractmethod
    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        """
        Run the agent's logic.

        Args:
            task: The task descriptor with input data.
            context: Shared context including memory store reference.

        Returns:
            The result to be stored in task.result.
        """
        ...

    def can_handle(self, task_name: str) -> bool:
        """Whether this agent type can handle the given task name."""
        return task_name == self.name
