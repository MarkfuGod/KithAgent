"""
Cron Scheduler — periodic task scheduler for smart agents.

Two scheduling strategies coexist:

1. **Fixed triggers** (preserved from v0.2):
   - after_scan:  Run after each filesystem full scan completes
   - after:<name>: Run after another agent completes (chained)
   - daily:       Run once per day at a specified time

2. **LLM-driven adaptive loop** (new in v0.3):
   The system gathers an activity snapshot, asks the LLM to decide
   which agents to run and at what interval, then dispatches and
   persists the decision for future context.  Falls back to sensible
   defaults when no LLM is available.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.agents.base import AgentTask
from src.kernel.config import CronConfig
from src.llm.base import LLMMessage

if TYPE_CHECKING:
    from src.kernel.daemon import SysAgentKernel

logger = logging.getLogger("agent_sys.kernel.cron")

_SCHEDULER_SYSTEM = """You are the adaptive scheduler for AgentOS, a system-level agent daemon.
Your job is to decide WHAT agents to run and WHEN based on the user's current activity.

Available agents and their purposes:
- triage: LLM-driven file importance classification — decides which files are worth summarizing based on their value for understanding the user. Should run before summarizer when there are many untriaged files.
- summarizer: Generates semantic file summaries. Modes: "light" (metadata-only LLM), "deep" (reads content). Respects triage results — only summarizes files marked 'high' or 'medium'.
- behavior_analyzer: Analyzes user's file activity, languages, work patterns.
- priority_classifier: Classifies files into P0 (hot) / P1 (warm) / P2 (cold).
- report_generator: Produces daily reports. Types: "daily", "quick", "brief".
- profile_builder: Builds a user profile from indexed data.

Given the activity snapshot, output a JSON scheduling decision:
{
  "mode": "light" | "deep",
  "next_interval_minutes": <integer between min and max>,
  "agents_to_run": [
    {"name": "<agent_name>", "input_data": {<optional overrides>}}
  ],
  "reasoning": "<1-2 sentences explaining your decision>"
}

Guidelines:
- If the user is actively modifying files right now, prefer "light" mode with shorter intervals (10-30 min) — run quick summaries and lightweight reports so data stays fresh without heavy load.
- If the user is inactive (night/idle), prefer "deep" mode with longer intervals — run thorough analysis, full summarization, profile rebuild.
- At the configured deep_analysis_hour, always trigger a comprehensive deep run.
- Consider the history of past decisions to avoid redundant work.
- The summarizer in light mode should set "mode": "light" in input_data; in deep mode set "mode": "deep".
- Report generator: use "report_type": "quick" for active hours, "report_type": "daily" for quiet hours.
- Don't run profile_builder more than once per day unless activity patterns changed significantly.

Output ONLY valid JSON."""


class CronScheduler:
    """Manages periodic execution of smart agents — fixed triggers + adaptive loop."""

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

            # Fixed triggers stay as-is
            if trigger == "daily":
                target_time = job.get("time", "09:00")
                t = asyncio.create_task(self._daily_loop(agent_name, target_time))
                self._tasks.append(t)
                logger.info("Cron: %s daily at %s", agent_name, target_time)

            elif trigger == "after_scan":
                t = asyncio.create_task(self._after_scan_loop(agent_name))
                self._tasks.append(t)
                logger.info("Cron: %s after each filesystem scan", agent_name)

            elif trigger.startswith("after:"):
                dependency = trigger.split(":", 1)[1]
                t = asyncio.create_task(self._after_agent_loop(agent_name, dependency))
                self._tasks.append(t)
                logger.info("Cron: %s after %s completes", agent_name, dependency)

            # interval and weekly triggers are now handled by the adaptive loop

        # Start the adaptive dispatch loop
        if self.config.adaptive.enabled:
            t = asyncio.create_task(self._adaptive_loop())
            self._tasks.append(t)
            logger.info("Cron: adaptive scheduling enabled (default interval: %dm)",
                        self.config.adaptive.default_interval_minutes)
        else:
            # Fallback: run old-style interval loops for interval/weekly jobs
            for job in self.config.jobs:
                trigger = job.get("trigger", "")
                agent_name = job.get("agent", "")
                if trigger == "interval":
                    hours = job.get("interval_hours", 6)
                    t = asyncio.create_task(self._interval_loop(agent_name, hours))
                    self._tasks.append(t)
                    logger.info("Cron: %s every %.1f hours (non-adaptive)", agent_name, hours)
                elif trigger == "weekly":
                    t = asyncio.create_task(self._interval_loop(agent_name, 168))
                    self._tasks.append(t)
                    logger.info("Cron: %s weekly (non-adaptive)", agent_name)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        logger.info("Cron scheduler stopped")

    # ── Adaptive loop (new in v0.3) ────────────────────────────

    async def _adaptive_loop(self) -> None:
        """LLM-driven scheduling: gather snapshot → LLM decides → dispatch → sleep."""
        await asyncio.sleep(30)  # let subsystems settle on boot

        interval = self.config.adaptive.default_interval_minutes

        while self._running:
            try:
                snapshot = await self._gather_activity_snapshot()
                decision = await self._llm_decide_schedule(snapshot)

                # Dispatch recommended agents, skipping ones already running or very recently completed
                scheduler = self.kernel.get_scheduler()
                active_names = set()
                if scheduler:
                    active_names = {t.name for t in scheduler._active_tasks.values()}

                for agent_spec in decision.get("agents_to_run", []):
                    agent_name = agent_spec.get("name", "")
                    input_data = agent_spec.get("input_data", {})
                    if not agent_name:
                        continue
                    if agent_name in active_names:
                        logger.debug("Skipping %s — already running", agent_name)
                        continue
                    last = self._last_run.get(agent_name, 0)
                    if time.time() - last < 60:
                        logger.debug("Skipping %s — ran %.0fs ago", agent_name, time.time() - last)
                        continue
                    await self._dispatch(agent_name, input_data=input_data)

                # Persist decision for future LLM context
                await self._persist_decision(decision, snapshot)

                # Use LLM-recommended interval, clamped to config bounds
                interval = max(
                    self.config.adaptive.min_interval_minutes,
                    min(
                        decision.get("next_interval_minutes", interval),
                        self.config.adaptive.max_interval_minutes,
                    ),
                )

                logger.info(
                    "Adaptive cycle: mode=%s, dispatched=%d agents, next in %dm — %s",
                    decision.get("mode", "?"),
                    len(decision.get("agents_to_run", [])),
                    interval,
                    decision.get("reasoning", ""),
                )

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Adaptive loop error: %s", e, exc_info=True)
                interval = self.config.adaptive.default_interval_minutes

            try:
                await asyncio.sleep(interval * 60)
            except asyncio.CancelledError:
                return

    async def _gather_activity_snapshot(self) -> dict:
        """Collect activity data for the LLM to analyze."""
        memory = self.kernel.get_memory()
        if not memory:
            return {}

        mod_rate_30m = await memory.get_modification_rate(minutes=30)
        mod_rate_6h = await memory.get_modification_rate(minutes=360)
        recent_files = await memory.get_recently_modified_files(hours=1, limit=20)
        mem_stats = await memory.stats()
        past_decisions = await memory.get_recent_scheduling_decisions(limit=5)

        now = datetime.now()
        return {
            "current_time": now.strftime("%Y-%m-%d %H:%M"),
            "current_hour": now.hour,
            "deep_analysis_hour": self.config.adaptive.deep_analysis_hour,
            "files_modified_last_30min": mod_rate_30m,
            "files_modified_last_6h": mod_rate_6h,
            "recent_files_sample": [f["path"].split("/")[-1] for f in recent_files[:10]],
            "indexed_files_total": mem_stats.get("indexed_files", 0),
            "knowledge_entries": mem_stats.get("knowledge_entries", 0),
            "past_decisions": [
                json.loads(d["content"]) if isinstance(d.get("content"), str) else d.get("content", {})
                for d in past_decisions
            ],
            "interval_bounds": {
                "min": self.config.adaptive.min_interval_minutes,
                "max": self.config.adaptive.max_interval_minutes,
            },
        }

    async def _llm_decide_schedule(self, snapshot: dict) -> dict:
        """Ask the LLM to decide the scheduling strategy. Falls back to defaults."""
        llm = self.kernel.get_llm()

        if llm and llm.available_providers():
            try:
                resp = await llm.complete(
                    messages=[
                        LLMMessage(role="system", content=_SCHEDULER_SYSTEM),
                        LLMMessage(role="user", content=json.dumps(snapshot, indent=2, default=str)),
                    ],
                    task_type="classify",
                    max_tokens=500,
                    temperature=0.3,
                )
                decision = json.loads(resp.content.strip())
                return decision
            except Exception as e:
                logger.warning("LLM scheduling decision failed: %s — using defaults", e)

        return self._default_decision(snapshot)

    def _default_decision(self, snapshot: dict) -> dict:
        """Rule-based fallback when no LLM is available."""
        hour = snapshot.get("current_hour", 12)
        mod_rate = snapshot.get("files_modified_last_30min", 0)
        deep_hour = self.config.adaptive.deep_analysis_hour

        if hour == deep_hour:
            return {
                "mode": "deep",
                "next_interval_minutes": self.config.adaptive.max_interval_minutes,
                "agents_to_run": [
                    {"name": "triage", "input_data": {"time_budget": 180, "timeout": 300}},
                    {"name": "summarizer", "input_data": {"mode": "deep", "time_budget": 240, "timeout": 300}},
                    {"name": "behavior_analyzer", "input_data": {"timeout": 300}},
                    {"name": "priority_classifier", "input_data": {}},
                    {"name": "profile_builder", "input_data": {"timeout": 300}},
                    {"name": "report_generator", "input_data": {"report_type": "daily", "timeout": 300}},
                ],
                "reasoning": f"Deep analysis hour ({deep_hour}:00) — running all agents in deep mode.",
            }

        if mod_rate > 3:
            return {
                "mode": "light",
                "next_interval_minutes": self.config.adaptive.min_interval_minutes,
                "agents_to_run": [
                    {"name": "summarizer", "input_data": {"mode": "light", "batch_size": 50, "time_budget": 60, "timeout": 120}},
                    {"name": "report_generator", "input_data": {"report_type": "quick", "timeout": 120}},
                ],
                "reasoning": f"User active ({mod_rate} files in 30min) — light mode, short interval.",
            }

        return {
            "mode": "deep",
            "next_interval_minutes": self.config.adaptive.default_interval_minutes * 2,
            "agents_to_run": [
                {"name": "summarizer", "input_data": {"mode": "deep", "time_budget": 180, "timeout": 240}},
                {"name": "behavior_analyzer", "input_data": {"timeout": 300}},
            ],
            "reasoning": f"User quiet ({mod_rate} files in 30min) — deep mode, longer interval.",
        }

    async def _persist_decision(self, decision: dict, snapshot: dict) -> None:
        """Store the scheduling decision in knowledge DB for future LLM context."""
        memory = self.kernel.get_memory()
        if not memory:
            return

        record = {
            "timestamp": time.time(),
            "mode": decision.get("mode"),
            "next_interval_minutes": decision.get("next_interval_minutes"),
            "agents_dispatched": [a.get("name") for a in decision.get("agents_to_run", [])],
            "reasoning": decision.get("reasoning", ""),
            "activity_snapshot": {
                "files_modified_30m": snapshot.get("files_modified_last_30min", 0),
                "hour": snapshot.get("current_hour"),
            },
        }

        await memory.store_knowledge(
            kid=f"scheduling_decision_{int(time.time())}",
            category="scheduling_decision",
            content=json.dumps(record, ensure_ascii=False),
            metadata={"mode": decision.get("mode")},
        )

    # ── Fixed trigger loops (preserved from v0.2) ─────────────

    async def _interval_loop(self, agent_name: str, hours: float) -> None:
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
        # Default overrides for agents that need longer timeouts
        _after_scan_overrides = {
            "triage": {"timeout": 300, "time_budget": 240},
            "summarizer": {"timeout": 300, "time_budget": 240, "mode": "deep"},
        }
        while self._running:
            await asyncio.sleep(10)
            fs = self.kernel.get_filesystem()
            if fs and hasattr(fs, "_stats"):
                current_scan = fs._stats.get("last_scan", 0.0)
                if current_scan > last_scan_time and last_scan_time > 0:
                    logger.info("Cron: scan completed, triggering %s", agent_name)
                    overrides = _after_scan_overrides.get(agent_name, {})
                    await self._dispatch(agent_name, input_data=overrides)
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

    _MIN_TIMEOUTS: dict[str, int] = {
        "triage": 300,
        "summarizer": 300,
        "behavior_analyzer": 300,
        "profile_builder": 300,
        "report_generator": 300,
        "priority_classifier": 180,
    }

    async def _dispatch(self, agent_name: str, input_data: dict | None = None) -> None:
        """Submit a task to the scheduler for the given agent."""
        scheduler = self.kernel.get_scheduler()
        if not scheduler:
            return

        data = dict(input_data or {})
        min_t = self._MIN_TIMEOUTS.get(agent_name, 120)
        if data.get("timeout", 0) < min_t:
            data["timeout"] = min_t

        task = AgentTask(
            name=agent_name,
            priority=2,
            input_data=data,
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
            "adaptive": self.config.adaptive.enabled,
            "jobs": len(self.config.jobs),
            "active_tasks": len(self._tasks),
            "last_run": dict(self._last_run),
        }
