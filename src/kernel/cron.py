"""
Cron Scheduler — periodic task scheduler for smart agents.

Trigger types:
  - interval:    Run every N hours
  - daily:       Run once per day at a specified time
  - weekly:      Run once per week
  - after_scan:  Run after each filesystem full scan completes
  - after:<name>: Run after another agent completes (chained)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.agents.base import AgentTask
from src.kernel.config import CronConfig

if TYPE_CHECKING:
    from src.kernel.daemon import SysAgentKernel

logger = logging.getLogger("agent_sys.kernel.cron")


class CronScheduler:
    """Manages periodic execution of smart agents."""

    def __init__(self, config: CronConfig, kernel: SysAgentKernel):
        self.config = config
        self.kernel = kernel
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._last_run: dict[str, float] = {}

    async def start(self) -> None:
        if not self.config.enabled:
            logger.info("Cron scheduler disabled")
            return

        self._running = True

        for job in self.config.jobs:
            agent_name = job.get("agent", "")
            trigger = job.get("trigger", "interval")

            if not agent_name:
                continue

            if trigger == "interval":
                hours = job.get("interval_hours", 6)
                t = asyncio.create_task(self._interval_loop(agent_name, hours))
                self._tasks.append(t)
                logger.info("Cron: %s every %.1f hours", agent_name, hours)

            elif trigger == "daily":
                target_time = job.get("time", "09:00")
                t = asyncio.create_task(self._daily_loop(agent_name, target_time))
                self._tasks.append(t)
                logger.info("Cron: %s daily at %s", agent_name, target_time)

            elif trigger == "weekly":
                t = asyncio.create_task(self._interval_loop(agent_name, 168))  # 7 * 24
                self._tasks.append(t)
                logger.info("Cron: %s weekly", agent_name)

            elif trigger == "after_scan":
                t = asyncio.create_task(self._after_scan_loop(agent_name))
                self._tasks.append(t)
                logger.info("Cron: %s after each filesystem scan", agent_name)

            elif trigger.startswith("after:"):
                dependency = trigger.split(":", 1)[1]
                t = asyncio.create_task(self._after_agent_loop(agent_name, dependency))
                self._tasks.append(t)
                logger.info("Cron: %s after %s completes", agent_name, dependency)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        logger.info("Cron scheduler stopped")

    # ── Trigger loops ─────────────────────────────────────────

    async def _interval_loop(self, agent_name: str, hours: float) -> None:
        # Wait a bit on first boot so other subsystems settle
        await asyncio.sleep(30)
        while self._running:
            await self._dispatch(agent_name)
            await asyncio.sleep(hours * 3600)

    async def _daily_loop(self, agent_name: str, target_time: str) -> None:
        while self._running:
            now = datetime.now()
            try:
                hour, minute = map(int, target_time.split(":"))
            except ValueError:
                hour, minute = 9, 0

            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target = target.replace(day=target.day + 1)

            wait_seconds = (target - now).total_seconds()
            logger.debug("Cron daily %s: next run in %.0f seconds", agent_name, wait_seconds)

            try:
                await asyncio.sleep(wait_seconds)
            except asyncio.CancelledError:
                return

            if self._running:
                await self._dispatch(agent_name)

    async def _after_scan_loop(self, agent_name: str) -> None:
        """Poll filesystem watcher stats to detect scan completions."""
        last_scan_time = 0.0
        while self._running:
            await asyncio.sleep(10)
            fs = self.kernel.get_filesystem()
            if fs and hasattr(fs, "_stats"):
                current_scan = fs._stats.get("last_scan", 0.0)
                if current_scan > last_scan_time and last_scan_time > 0:
                    logger.info("Cron: scan completed, triggering %s", agent_name)
                    await self._dispatch(agent_name)
                last_scan_time = current_scan

    async def _after_agent_loop(self, agent_name: str, dependency: str) -> None:
        """Poll for completion of a dependency agent, then run."""
        while self._running:
            await asyncio.sleep(15)
            dep_last = self._last_run.get(dependency, 0)
            my_last = self._last_run.get(agent_name, 0)
            if dep_last > my_last and dep_last > 0:
                logger.info("Cron: %s completed, triggering %s", dependency, agent_name)
                await self._dispatch(agent_name)

    # ── Dispatch helper ───────────────────────────────────────

    async def _dispatch(self, agent_name: str) -> None:
        """Submit a task to the scheduler for the given agent."""
        scheduler = self.kernel.get_scheduler()
        if not scheduler:
            return

        task = AgentTask(
            name=agent_name,
            priority=2,  # low priority — background work
            input_data={},
            caller="cron",
        )
        context = {
            "memory": self.kernel.get_memory(),
            "scheduler": scheduler,
            "filesystem": self.kernel.get_filesystem(),
            "kernel": self.kernel,
            "llm": self.kernel.get_llm(),
        }

        try:
            result = await scheduler.submit_and_wait(task, context)
            self._last_run[agent_name] = time.time()
            logger.info("Cron task %s completed: %s", agent_name, result.state.value)
        except Exception as e:
            logger.error("Cron task %s failed: %s", agent_name, e)

    def status(self) -> dict:
        return {
            "enabled": self.config.enabled,
            "running": self._running,
            "jobs": len(self.config.jobs),
            "active_tasks": len(self._tasks),
            "last_run": dict(self._last_run),
        }
