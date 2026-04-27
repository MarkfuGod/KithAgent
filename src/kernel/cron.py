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
- triage: LLM-driven file importance classification — decides which files are worth summarizing. Should run FIRST when there are untriaged files.
- summarizer: Generates semantic file summaries. Modes: "light" (metadata-only LLM), "deep" (reads content). Respects triage results.
- behavior_analyzer: Analyzes user's file activity, languages, work patterns.
- priority_classifier: Classifies files into P0 (hot) / P1 (warm) / P2 (cold).
- report_generator: Produces daily reports. Types: "daily", "quick", "brief".
- profile_builder: Builds a user profile from indexed data.
- rag_indexer: Low-priority background chunk indexing for hybrid RAG. Run only after first insight/startup delay.

Output a JSON scheduling decision. You can use "stages" for sequential groups
(each stage runs in order, agents within a stage run in parallel):

{
  "mode": "light" | "deep",
  "next_interval_minutes": <integer between min and max>,
  "stages": [
    [{"name": "triage", "input_data": {}}],
    [{"name": "summarizer", "input_data": {"mode": "deep"}}, {"name": "behavior_analyzer", "input_data": {}}]
  ],
  "reasoning": "<1-2 sentences explaining your decision>"
}

If you don't need staging, you can use the flat format instead:
{
  "mode": "...",
  "next_interval_minutes": ...,
  "agents_to_run": [{"name": "...", "input_data": {}}],
  "reasoning": "..."
}

The user has configured a "scheduling_strategy" (aggressive/balanced/quiet) that tells
you how eager the system should be. Respect it:
- "aggressive": run triage and summarize every cycle, all agents frequently
- "balanced" (default): triage every cycle, summarize when idle or deep hour, reports daily
- "quiet": only run agents during deep_analysis_hour, minimize resource usage

The snapshot includes enriched context:
- "triage_stats": counts per status (untriaged, high, medium, low, skip)
- "summary_progress": {total, summarized, pending, percent}
- "embedding": provider info and availability
- "llm": available providers, cumulative calls/tokens

Guidelines:
- ALWAYS include triage first when there are many untriaged files (check triage_stats.untriaged).
- If summary_progress.percent is low and there are triaged files, prioritize summarizer.
- If the user is actively modifying files, prefer "light" mode with short intervals.
- If the user is inactive (night/idle), prefer "deep" mode with longer intervals.
- At the configured deep_analysis_hour, trigger a comprehensive deep run.
- Use stages to express dependencies: triage before summarizer, summarizer before behavior_analyzer.
- Agents within the same stage CAN run in parallel (fan-out).
- Report generator: "report_type": "quick" for active hours, "daily" for quiet hours.
- Consider embedding availability: if embeddings are ready, summarizer will auto-compute them.

Output ONLY valid JSON."""


SCHEDULING_STRATEGIES: dict[str, dict[str, Any]] = {
    "aggressive": {
        "description": "Always triage + summarize on every cycle. Good for initial indexing or catching up.",
        "triage": "always",        # always | active_only | deep_hour_only
        "summarize": "always",     # always | active_only | deep_hour_only
        "report": "always",        # always | daily_only | never
        "behavior": "frequent",    # frequent | daily | deep_hour_only
        "profile": "daily",        # daily | weekly | deep_hour_only
        "priority": "always",      # always | after_behavior | deep_hour_only
    },
    "balanced": {
        "description": "Triage every cycle, summarize when idle or deep hour. Default.",
        "triage": "always",
        "summarize": "active_only",
        "report": "daily_only",
        "behavior": "daily",
        "profile": "weekly",
        "priority": "after_behavior",
    },
    "quiet": {
        "description": "Only run during deep hour. Minimal resource usage.",
        "triage": "deep_hour_only",
        "summarize": "deep_hour_only",
        "report": "daily_only",
        "behavior": "deep_hour_only",
        "profile": "deep_hour_only",
        "priority": "deep_hour_only",
    },
}


class CronScheduler:
    """Manages periodic execution of smart agents — fixed triggers + adaptive loop."""

    def __init__(self, config: CronConfig, kernel: SysAgentKernel):
        self.config = config
        self.kernel = kernel
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._last_run: dict[str, float] = {}
        self._started_at = time.time()

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
                if not await self._first_insight_ready():
                    await asyncio.sleep(30)
                    continue

                snapshot = await self._gather_activity_snapshot()
                decision = await self._llm_decide_schedule(snapshot)

                scheduler = self.kernel.get_scheduler()
                active_names = set()
                if scheduler:
                    active_names = {t.name for t in scheduler._active_tasks.values()}

                decision = await self._maybe_add_rag_indexer(decision, snapshot, active_names)

                stages = decision.get("stages")
                if stages and isinstance(stages, list):
                    await self._dispatch_stages(stages, active_names)
                else:
                    for agent_spec in self._triage_first(decision.get("agents_to_run", [])):
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

        # Triage stats: how many files in each triage bucket
        triage_stats = {}
        try:
            triage_stats = await memory.get_triage_stats()
        except Exception:
            pass

        # Summary progress: how many files have been summarized
        summary_progress = {}
        try:
            total = mem_stats.get("indexed_files", 0)
            summarized = await memory.count_summarized_files()
            summary_progress = {
                "total": total,
                "summarized": summarized,
                "pending": total - summarized,
                "percent": round(summarized / total * 100, 1) if total > 0 else 0,
            }
        except Exception:
            pass

        # Embedding status
        embedding_info = {}
        try:
            from src.memory.embeddings import get_provider_info, is_available
            embedding_info = {
                **get_provider_info(),
                "available": is_available(),
            }
        except Exception:
            pass

        # LLM router status
        llm_status = {}
        llm = self.kernel.get_llm()
        if llm:
            llm_status = {
                "available_providers": llm.available_providers(),
                "total_calls": llm._total_calls,
                "total_tokens": llm._total_tokens,
            }

        rag_status = {}
        try:
            rag_cfg = getattr(self.kernel.config.memory, "rag", None)
            if rag_cfg and getattr(rag_cfg, "enabled", True):
                pending = await memory.get_files_needing_rag_index(
                    limit=1,
                    allowed_triage_statuses=getattr(rag_cfg, "allowed_triage_statuses", ["high", "medium"]),
                    max_file_size_bytes=getattr(rag_cfg, "max_file_size_mb", 5) * 1024 * 1024,
                )
                rag_status = {
                    "enabled": True,
                    "pending": len(pending),
                    "chunks": await memory.count_document_chunks(),
                    "initial_delay_seconds": getattr(rag_cfg, "initial_delay_seconds", 600),
                }
            else:
                rag_status = {"enabled": False}
        except Exception:
            rag_status = {}

        strategy = getattr(self.config.adaptive, "strategy", "balanced")
        now = datetime.now()
        return {
            "current_time": now.strftime("%Y-%m-%d %H:%M"),
            "current_hour": now.hour,
            "deep_analysis_hour": self.config.adaptive.deep_analysis_hour,
            "scheduling_strategy": strategy,
            "strategy_description": SCHEDULING_STRATEGIES.get(strategy, {}).get("description", ""),
            "files_modified_last_30min": mod_rate_30m,
            "files_modified_last_6h": mod_rate_6h,
            "recent_files_sample": [f["path"].split("/")[-1] for f in recent_files[:10]],
            "indexed_files_total": mem_stats.get("indexed_files", 0),
            "knowledge_entries": mem_stats.get("knowledge_entries", 0),
            "triage_stats": triage_stats,
            "summary_progress": summary_progress,
            "embedding": embedding_info,
            "rag": rag_status,
            "llm": llm_status,
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

    # Which agents require a working LLM to make meaningful progress.
    # When no LLM is available we silently drop these from the fallback
    # schedule so we don't busy-loop dispatching tasks that will return
    # `{"skipped_reason": "no_llm"}`.
    _LLM_REQUIRED_AGENTS = frozenset({
        "summarizer",
        "behavior_analyzer",
        "report_generator",
        "profile_builder",
        "triage",  # still runs (rule-based), but gets lower priority
    })

    def _default_decision(self, snapshot: dict) -> dict:
        """Rule-based fallback when no LLM is available.

        Uses the configured scheduling strategy to decide which agents run.
        Strategies: aggressive (always everything), balanced (default),
        quiet (deep-hour only).
        """
        hour = snapshot.get("current_hour", 12)
        mod_rate = snapshot.get("files_modified_last_30min", 0)
        deep_hour = self.config.adaptive.deep_analysis_hour
        strategy_name = getattr(self.config.adaptive, "strategy", "balanced")
        strat = SCHEDULING_STRATEGIES.get(strategy_name, SCHEDULING_STRATEGIES["balanced"])

        # Is there any working LLM backend at all?
        llm = self.kernel.get_llm()
        llm_available = bool(llm and llm.available_providers())

        is_deep_hour = (hour == deep_hour)
        is_active = (mod_rate > 3)

        def _should_run(policy: str) -> bool:
            if policy == "always":
                return True
            if policy == "active_only":
                return is_active or is_deep_hour
            if policy == "deep_hour_only":
                return is_deep_hour
            if policy == "daily" or policy == "daily_only":
                return is_deep_hour
            if policy == "weekly":
                return is_deep_hour and datetime.now().weekday() == 0
            if policy == "frequent":
                return True
            if policy == "after_behavior":
                return is_deep_hour
            if policy == "never":
                return False
            return True

        agents: list[dict] = []

        def _maybe_add(agent_name: str, spec: dict) -> None:
            if not llm_available and agent_name in self._LLM_REQUIRED_AGENTS and agent_name != "triage":
                return
            agents.append({"name": agent_name, "input_data": spec})

        if _should_run(strat.get("triage", "always")):
            budget = 180 if is_deep_hour else (60 if is_active else 120)
            # Triage runs even without an LLM (rule-based pass marks the
            # obvious noise and stamps the rest as 'unknown').
            _maybe_add("triage", {"time_budget": budget, "timeout": 300})

        if _should_run(strat.get("summarize", "active_only")):
            if is_active and not is_deep_hour:
                _maybe_add("summarizer", {"mode": "light", "batch_size": 50, "time_budget": 60, "timeout": 120})
            else:
                _maybe_add("summarizer", {"mode": "deep", "time_budget": 240, "timeout": 300})

        if _should_run(strat.get("behavior", "daily")):
            _maybe_add("behavior_analyzer", {"timeout": 300})

        if _should_run(strat.get("priority", "after_behavior")):
            # priority_classifier is rule-based (date buckets), runs without LLM.
            agents.append({"name": "priority_classifier", "input_data": {}})

        if _should_run(strat.get("profile", "weekly")):
            _maybe_add("profile_builder", {"timeout": 300})

        if _should_run(strat.get("report", "daily_only")):
            rtype = "daily" if is_deep_hour else ("quick" if is_active else "daily")
            _maybe_add("report_generator", {"report_type": rtype, "timeout": 300})

        if not llm_available:
            # No LLM configured → back off aggressively. The only useful
            # work we can do is rule-based triage + priority classification,
            # and doing that every 10 minutes is wasteful.
            mode = "rules_only"
            interval = self.config.adaptive.max_interval_minutes
            reason = (
                f"No LLM configured (rules_only mode), "
                f"strategy={strategy_name}, backing off to {interval}m"
            )
        elif is_deep_hour:
            mode = "deep"
            interval = self.config.adaptive.max_interval_minutes
            reason = f"Deep hour ({deep_hour}:00), strategy={strategy_name}"
        elif is_active:
            mode = "light"
            interval = self.config.adaptive.min_interval_minutes
            reason = f"Active ({mod_rate} files/30min), strategy={strategy_name}"
        else:
            mode = "deep"
            interval = self.config.adaptive.default_interval_minutes * 2
            reason = f"Quiet ({mod_rate} files/30min), strategy={strategy_name}"

        return {
            "mode": mode,
            "next_interval_minutes": interval,
            "agents_to_run": agents,
            "reasoning": reason,
            "strategy": strategy_name,
        }

    async def _maybe_add_rag_indexer(
        self,
        decision: dict,
        snapshot: dict,
        active_names: set[str],
    ) -> dict:
        """Append delayed low-priority RAG indexing without asking chat to wait."""
        rag_cfg = getattr(self.kernel.config.memory, "rag", None)
        if not rag_cfg or not getattr(rag_cfg, "enabled", True):
            return decision
        if "rag_indexer" in active_names:
            return decision
        if time.time() - self._started_at < getattr(rag_cfg, "initial_delay_seconds", 600):
            return decision
        if time.time() - self._last_run.get("rag_indexer", 0) < 600:
            return decision
        if not (snapshot.get("rag") or {}).get("pending"):
            return decision

        spec = {
            "name": "rag_indexer",
            "input_data": {
                "batch_size": getattr(rag_cfg, "batch_size", 20),
                "embedding_batch_size": getattr(rag_cfg, "embedding_batch_size", 32),
                "time_budget": getattr(rag_cfg, "time_budget_seconds", 90),
                "timeout": max(180, getattr(rag_cfg, "time_budget_seconds", 90) + 60),
            },
        }
        if isinstance(decision.get("stages"), list):
            decision["stages"].append([spec])
        else:
            agents = decision.setdefault("agents_to_run", [])
            if isinstance(agents, list) and not any(a.get("name") == "rag_indexer" for a in agents):
                agents.append(spec)
        return decision

    @staticmethod
    def _triage_first(agent_specs: list[dict]) -> list[dict]:
        """Force triage before summarizer even if the scheduler LLM reversed them."""
        if not isinstance(agent_specs, list):
            return []
        return sorted(
            agent_specs,
            key=lambda spec: {"triage": 0, "summarizer": 1}.get(spec.get("name", ""), 2),
        )

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
            knowledge_id=f"scheduling_decision_{int(time.time())}",
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
        first_scan_seen = False
        _after_scan_overrides = {
            "triage": {"timeout": 300, "time_budget": 240},
            "summarizer": {"timeout": 300, "time_budget": 240, "mode": "deep"},
        }
        while self._running:
            await asyncio.sleep(10)
            # Skip LLM-dependent agents entirely if no LLM is configured.
            if agent_name in self._LLM_REQUIRED_AGENTS and agent_name != "triage":
                llm = self.kernel.get_llm()
                if not llm or not llm.available_providers():
                    continue
            fs = self.kernel.get_filesystem()
            if fs and hasattr(fs, "_stats"):
                current_scan = fs._stats.get("last_scan", 0.0)
                if current_scan > last_scan_time:
                    if first_scan_seen:
                        if not await self._first_insight_ready():
                            logger.info(
                                "Cron: scan completed; deferring %s until First Insight is ready",
                                agent_name,
                            )
                            last_scan_time = current_scan
                            continue
                        logger.info("Cron: scan completed, triggering %s", agent_name)
                        overrides = _after_scan_overrides.get(agent_name, {})
                        await self._dispatch(agent_name, input_data=overrides)
                    else:
                        logger.info(
                            "Cron: initial scan completed; deferring %s so First Insight can run first",
                            agent_name,
                        )
                    first_scan_seen = True
                    last_scan_time = current_scan

    async def _first_insight_ready(self) -> bool:
        """Give the product onboarding path the first few minutes after boot."""
        memory = self.kernel.get_memory()
        if memory:
            try:
                runs = await memory.list_insight_runs(run_type="first_insight", limit=1)
                if runs and runs[0].get("status") == "completed":
                    return True
            except Exception:
                pass
        # Legacy/daemon-only sessions should still make progress eventually.
        return (time.time() - self._started_at) > 600

    async def _after_agent_loop(self, agent_name: str, dependency: str) -> None:
        """Poll for completion of a dependency agent, then run."""
        while self._running:
            await asyncio.sleep(15)
            dep_last = self._last_run.get(dependency, 0)
            my_last = self._last_run.get(agent_name, 0)
            if dep_last > my_last and dep_last > 0:
                logger.info("Cron: %s completed, triggering %s", dependency, agent_name)
                await self._dispatch(agent_name)

    async def _dispatch_stages(self, stages: list[list[dict]], active_names: set[str]) -> None:
        """Execute a DAG of agent stages with result-gated transitions.

        Stages run sequentially; agents within each stage run in parallel via fan_out.
        After each stage, results are summarized and (if LLM is available) used to
        adjust the next stage's parameters — e.g. if triage found many high-priority
        files, the summarizer gets a larger time budget and deep mode.
        """
        scheduler = self.kernel.get_scheduler()
        if not scheduler:
            return

        stage_results: list[dict] = []

        for stage_idx, stage in enumerate(stages):
            if not isinstance(stage, list):
                continue

            # If we have prior stage results and an LLM, ask it to gate/adjust
            if stage_idx > 0 and stage_results:
                adjusted = await self._gate_next_stage(stage_idx, stage, stage_results)
                if adjusted is not None:
                    if not adjusted:
                        logger.info("Stage %d: LLM gate decided to skip remaining stages", stage_idx)
                        break
                    stage = adjusted

            tasks_to_fan = []
            for agent_spec in stage:
                agent_name = agent_spec.get("name", "")
                input_data = agent_spec.get("input_data", {})
                if not agent_name:
                    continue
                if agent_name in active_names:
                    logger.debug("Stage %d: skipping %s — already running", stage_idx, agent_name)
                    continue
                last = self._last_run.get(agent_name, 0)
                if time.time() - last < 60:
                    continue

                min_t = self._MIN_TIMEOUTS.get(agent_name, 120)
                data = dict(input_data)
                if data.get("timeout", 0) < min_t:
                    data["timeout"] = min_t

                tasks_to_fan.append(AgentTask(
                    name=agent_name,
                    priority=2,
                    input_data=data,
                    caller="cron_stage",
                ))

            if not tasks_to_fan:
                stage_results.append({"stage": stage_idx, "agents": [], "skipped": True})
                continue

            current_results = {"stage": stage_idx, "agents": []}

            if len(tasks_to_fan) == 1:
                await self._dispatch(tasks_to_fan[0].name, input_data=tasks_to_fan[0].input_data)
                current_results["agents"].append({
                    "name": tasks_to_fan[0].name,
                    "state": "dispatched",
                })
            else:
                context = {
                    "memory": self.kernel.get_memory(),
                    "scheduler": scheduler,
                    "filesystem": self.kernel.get_filesystem(),
                    "kernel": self.kernel,
                    "llm": self.kernel.get_llm(),
                    "event_bus": self.kernel.get_event_bus(),
                }
                logger.info("Stage %d: fan_out %d agents: %s",
                            stage_idx, len(tasks_to_fan),
                            [t.name for t in tasks_to_fan])
                results = await scheduler.fan_out(tasks_to_fan, context)
                for t in results:
                    self._last_run[t.name] = time.time()
                    result_summary = self._summarize_task_result(t)
                    current_results["agents"].append(result_summary)
                    logger.info("Stage %d: %s → %s (%.2fs)",
                                stage_idx, t.name, t.state.value, t.elapsed() or 0)

            stage_results.append(current_results)

    async def _gate_next_stage(
        self, stage_idx: int, planned_stage: list[dict], prior_results: list[dict],
    ) -> list[dict] | None:
        """Ask the LLM whether to proceed with the next stage, and how to adjust its params.

        Returns:
          - adjusted stage spec (list of agent dicts) if LLM wants to proceed with changes
          - the original planned_stage unchanged if no LLM or LLM says proceed as-is
          - empty list [] if LLM says skip this stage
          - None on error (falls through to original plan)
        """
        llm = self.kernel.get_llm()
        if not llm or not llm.available_providers():
            return None

        gate_prompt = (
            f"Stage {stage_idx} is about to run. Prior stage results:\n"
            f"{json.dumps(prior_results, indent=2, default=str)}\n\n"
            f"Planned agents for stage {stage_idx}:\n"
            f"{json.dumps(planned_stage, indent=2)}\n\n"
            "Based on the prior results, should we:\n"
            "1. Proceed as planned (return the stage unchanged)\n"
            "2. Adjust parameters (e.g. increase time_budget, change mode)\n"
            "3. Skip this stage entirely\n\n"
            "Return JSON: {\"action\": \"proceed\"|\"adjust\"|\"skip\", "
            "\"adjusted_stage\": [...] (only if action=adjust), "
            "\"reason\": \"...\"}\n"
            "Output ONLY valid JSON."
        )

        try:
            resp = await llm.complete(
                messages=[
                    LLMMessage(role="system", content=(
                        "You are the stage gate controller for AgentOS scheduling. "
                        "You decide whether to proceed, adjust, or skip the next stage "
                        "based on results from prior stages. Be concise."
                    )),
                    LLMMessage(role="user", content=gate_prompt),
                ],
                task_type="classify",
                max_tokens=300,
                temperature=0.2,
            )
            decision = json.loads(resp.content.strip())
            action = decision.get("action", "proceed")

            if action == "skip":
                logger.info("Gate: LLM decided to skip stage %d — %s", stage_idx, decision.get("reason", ""))
                return []
            elif action == "adjust":
                adjusted = decision.get("adjusted_stage", planned_stage)
                logger.info("Gate: LLM adjusted stage %d — %s", stage_idx, decision.get("reason", ""))
                return adjusted if isinstance(adjusted, list) else planned_stage
            else:
                return None
        except Exception as e:
            logger.debug("Gate decision failed for stage %d: %s — proceeding as planned", stage_idx, e)
            return None

    @staticmethod
    def _summarize_task_result(task: AgentTask) -> dict:
        """Extract a concise summary from a completed task for the gate LLM."""
        summary: dict[str, Any] = {
            "name": task.name,
            "state": task.state.value,
            "elapsed_s": round(task.elapsed() or 0, 1),
        }
        if task.result and isinstance(task.result, dict):
            for key in ("llm_triaged", "rule_based_skipped", "triage_distribution",
                        "summarized", "errors", "mode", "total_candidates",
                        "vision_files", "document_files", "hierarchical"):
                if key in task.result:
                    summary[key] = task.result[key]
        if task.error:
            summary["error"] = task.error
        return summary

    # ── Dispatch helper ───────────────────────────────────────

    _MIN_TIMEOUTS: dict[str, int] = {
        "triage": 300,
        "summarizer": 300,
        "behavior_analyzer": 300,
        "profile_builder": 300,
        "report_generator": 300,
        "priority_classifier": 180,
        "rag_indexer": 180,
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
            "event_bus": self.kernel.get_event_bus(),
        }

        try:
            result = await scheduler.submit_and_wait(task, context)
            self._last_run[agent_name] = time.time()
            logger.info("Cron task %s completed: %s", agent_name, result.state.value)
        except Exception as e:
            logger.error("Cron task %s failed: %s", agent_name, e)

    def status(self) -> dict:
        strategy = getattr(self.config.adaptive, "strategy", "balanced")
        return {
            "enabled": self.config.enabled,
            "running": self._running,
            "adaptive": self.config.adaptive.enabled,
            "strategy": strategy,
            "strategy_description": SCHEDULING_STRATEGIES.get(strategy, {}).get("description", ""),
            "jobs": len(self.config.jobs),
            "active_tasks": len(self._tasks),
            "last_run": dict(self._last_run),
        }
