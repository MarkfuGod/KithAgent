"""
Agent Scheduler — the 'process scheduler' of AgentOS.

Like an OS scheduler that manages threads across CPU cores,
this scheduler manages agent tasks across a worker pool.

Features:
  - Priority queue (high/normal/low)
  - Concurrency limit (like CPU core count)
  - Task lifecycle tracking
  - Agent registry (maps task names → agent implementations)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.agents.base import AgentState, AgentTask, BaseAgent
from src.agents.builtin import BUILTIN_AGENTS
from src.kernel.config import SchedulerConfig

logger = logging.getLogger("agent_sys.scheduler")


class AgentScheduler:
    """Priority-based agent task scheduler with concurrency control."""

    def __init__(self, config: SchedulerConfig):
        self.config = config
        self._agents: list[BaseAgent] = list(BUILTIN_AGENTS)
        self._task_queue: asyncio.PriorityQueue[tuple[int, float, AgentTask]] = asyncio.PriorityQueue()
        self._active_tasks: dict[str, AgentTask] = {}
        self._completed_tasks: list[AgentTask] = []
        self._semaphore = asyncio.Semaphore(config.max_concurrent_agents)
        self._running = False
        self._worker_task: asyncio.Task | None = None
        self._runner_tasks: dict[str, asyncio.Task] = {}
        self._total_dispatched = 0

    async def start(self) -> None:
        self._running = True
        self._worker_task = asyncio.create_task(self._dispatch_loop())
        logger.info(
            "Scheduler started: max_concurrent=%d, registered_agents=%d",
            self.config.max_concurrent_agents, len(self._agents),
        )

    async def stop(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        for runner in list(self._runner_tasks.values()):
            runner.cancel()
        if self._runner_tasks:
            await asyncio.gather(*self._runner_tasks.values(), return_exceptions=True)
            self._runner_tasks.clear()

        for task_id, task in self._active_tasks.items():
            task.state = AgentState.CANCELLED
        logger.info("Scheduler stopped")

    def register_agent(self, agent: BaseAgent) -> None:
        self._agents.append(agent)
        logger.info("Registered agent: %s", agent.name)

    async def submit(self, task: AgentTask, context: dict[str, Any]) -> AgentTask:
        """Submit a task for execution — like fork() + exec()."""
        task._context = context  # type: ignore[attr-defined]
        await self._task_queue.put((task.priority, task.created_at, task))
        logger.info("Task submitted: %s [%s] priority=%d", task.task_id, task.name, task.priority)
        return task

    async def submit_and_wait(self, task: AgentTask, context: dict[str, Any]) -> AgentTask:
        """Submit and block until completion — like a synchronous syscall."""
        event = asyncio.Event()
        task._done_event = event  # type: ignore[attr-defined]
        await self.submit(task, context)
        timeout = task.input_data.get("timeout", self.config.default_timeout_seconds)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            await self._timeout_task(task, "Timeout")
        return task

    async def fan_out(
        self,
        tasks: list[AgentTask],
        context: dict[str, Any],
        parent_task_id: str | None = None,
    ) -> list[AgentTask]:
        """Submit multiple tasks in parallel and wait for all to complete.

        Like Promise.all / asyncio.gather for agent tasks. Used by agents
        that want to spawn parallel sub-work (e.g. summarize code + docs + images
        concurrently).
        """
        events: list[tuple[AgentTask, asyncio.Event]] = []
        for t in tasks:
            if parent_task_id:
                t.parent_task_id = parent_task_id
            event = asyncio.Event()
            t._done_event = event  # type: ignore[attr-defined]
            await self.submit(t, context)
            events.append((t, event))

        async def _wait_one(task: AgentTask, evt: asyncio.Event) -> None:
            timeout = task.input_data.get("timeout", self.config.default_timeout_seconds)
            try:
                await asyncio.wait_for(evt.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                await self._timeout_task(task, "Timeout (fan_out)")

        await asyncio.gather(*[_wait_one(t, e) for t, e in events])
        return [t for t, _ in events]

    # ── dispatch loop ─────────────────────────────────────────

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                priority, _, task = await asyncio.wait_for(
                    self._task_queue.get(), timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            runner = asyncio.create_task(self._run_task(task))
            self._runner_tasks[task.task_id] = runner
            runner.add_done_callback(
                lambda _done, task_id=task.task_id: self._runner_tasks.pop(task_id, None)
            )

    async def _run_task(self, task: AgentTask) -> None:
        if getattr(task, "_cancel_requested", False):
            task.state = AgentState.FAILED
            task.error = task.error or "Timeout"
            task.completed_at = time.time()
            self._finalize(task)
            return

        agent = self._find_agent(task.name)
        if not agent:
            task.state = AgentState.FAILED
            task.error = f"No agent registered for task type: {task.name}"
            logger.error(task.error)
            self._finalize(task)
            return

        async with self._semaphore:
            task.state = AgentState.RUNNING
            task.started_at = time.time()
            self._active_tasks[task.task_id] = task
            self._total_dispatched += 1

            await self._emit_event("task.started", {
                "task_id": task.task_id, "name": task.name,
                "priority": task.priority, "caller": task.caller,
            }, task)

            try:
                context = getattr(task, "_context", {})
                timeout = task.input_data.get("timeout", self.config.default_timeout_seconds)
                result = await asyncio.wait_for(
                    agent.execute(task, context),
                    timeout=timeout,
                )
                task.result = result
                task.state = AgentState.COMPLETED
            except asyncio.TimeoutError:
                task.state = AgentState.FAILED
                task.error = "Agent execution timed out"
            except asyncio.CancelledError:
                if getattr(task, "_cancel_requested", False):
                    task.state = AgentState.FAILED
                    task.error = task.error or "Timeout"
                else:
                    task.state = AgentState.CANCELLED
                    task.error = task.error or "Cancelled"
            except Exception as e:
                task.state = AgentState.FAILED
                task.error = str(e)
                logger.exception("Task %s failed", task.task_id)
            finally:
                task.completed_at = time.time()
                self._active_tasks.pop(task.task_id, None)
                event_type = "task.completed" if task.state == AgentState.COMPLETED else "task.failed"
                await self._emit_event(event_type, {
                    "task_id": task.task_id, "name": task.name,
                    "state": task.state.value,
                    "elapsed_s": round(task.elapsed() or 0, 2),
                    "error": task.error,
                }, task)
                self._finalize(task)

    async def _timeout_task(self, task: AgentTask, error: str) -> None:
        """Cancel a timed-out queued/running task and finalize it once."""
        task._cancel_requested = True  # type: ignore[attr-defined]
        task.state = AgentState.FAILED
        task.error = error
        task.completed_at = time.time()
        self._active_tasks.pop(task.task_id, None)

        runner = self._runner_tasks.get(task.task_id)
        if runner and not runner.done():
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
            self._finalize(task)
        else:
            self._finalize(task)

    def _finalize(self, task: AgentTask) -> None:
        if getattr(task, "_finalized", False):
            return
        task._finalized = True  # type: ignore[attr-defined]
        self._completed_tasks.append(task)
        if len(self._completed_tasks) > 1000:
            self._completed_tasks = self._completed_tasks[-500:]

        done_event = getattr(task, "_done_event", None)
        if done_event:
            done_event.set()

        logger.info(
            "Task %s [%s] → %s (%.2fs)",
            task.task_id, task.name, task.state.value,
            task.elapsed() or 0,
        )

    async def _emit_event(self, event_type: str, data: dict, task: AgentTask) -> None:
        ctx = getattr(task, "_context", {})
        event_bus = ctx.get("event_bus")
        if event_bus:
            try:
                await event_bus.emit_dict(event_type, data)
            except Exception:
                pass

    def _find_agent(self, task_name: str) -> BaseAgent | None:
        for agent in self._agents:
            if agent.can_handle(task_name):
                return agent
        return None

    def status(self) -> dict:
        return {
            "running": self._running,
            "registered_agents": [a.name for a in self._agents],
            "queue_size": self._task_queue.qsize(),
            "active_tasks": len(self._active_tasks),
            "total_dispatched": self._total_dispatched,
            "max_concurrent": self.config.max_concurrent_agents,
        }

    def get_task(self, task_id: str) -> AgentTask | None:
        if task_id in self._active_tasks:
            return self._active_tasks[task_id]
        for t in reversed(self._completed_tasks):
            if t.task_id == task_id:
                return t
        return None
