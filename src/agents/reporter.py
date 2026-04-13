"""
ReportGeneratorAgent — produces structured reports for external agents.

Three report types:
  1. daily_report:   What changed today, progress summary, inferred TODOs
  2. project_profile: Per-project tech stack, key files, dependencies
  3. context_brief:   "Everything you need to know" for a new agent session

Reports are stored in the knowledge table and served via syscalls.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

from src.agents.base import AgentTask, BaseAgent
from src.llm.base import LLMMessage

logger = logging.getLogger("agent_sys.agents.reporter")

_DAILY_SYSTEM = """You are a daily report generator for AgentOS.
Given today's file activity, produce a concise daily report in JSON:
{
  "date": "YYYY-MM-DD",
  "summary": "1-2 sentence overview of the day",
  "files_changed": N,
  "active_projects": ["project dirs"],
  "key_changes": ["brief descriptions of notable changes"],
  "inferred_todos": ["things that seem in progress or need attention"]
}
Output ONLY valid JSON."""

_BRIEF_SYSTEM = """You are a context briefing generator for AgentOS.
Given the user's recent activity, behavior insights, and profile, produce a
JSON context brief that an AI agent can consume to quickly understand the user's
current state:
{
  "current_focus": "what the user is working on right now",
  "recent_activity": "summary of recent work",
  "key_files": ["most relevant files for current work"],
  "user_preferences": "known preferences and patterns",
  "suggested_context": "anything else a new agent session should know"
}
Output ONLY valid JSON."""


class ReportGeneratorAgent(BaseAgent):
    """Generate structured reports: daily, project profile, context brief."""
    name = "report_generator"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        report_type = task.input_data.get("report_type", "daily")

        if report_type == "daily":
            return await self._generate_daily(context)
        elif report_type == "project":
            return await self._generate_project_profile(context, task.input_data.get("project_dir"))
        elif report_type == "brief":
            return await self._generate_context_brief(context)
        else:
            return {"error": f"Unknown report type: {report_type}"}

    async def _generate_daily(self, context: dict[str, Any]) -> dict:
        memory = context["memory"]
        llm = context.get("llm")

        recent = await memory.get_recently_modified_files(hours=24, limit=100)
        dir_activity = await memory.get_directory_activity(depth=3)

        data_lines = [f"Date: {datetime.now().strftime('%Y-%m-%d')}",
                      f"Files modified in last 24h: {len(recent)}", ""]
        for f in recent[:30]:
            ts = datetime.fromtimestamp(f["modified_at"]).strftime("%H:%M") if f["modified_at"] else "?"
            data_lines.append(f"  [{ts}] {f['file_type']}  {f['path']}")

        data_lines.append("\nActive directories:")
        for d in dir_activity[:10]:
            data_lines.append(f"  {d['directory']}: {d['file_count']} files")

        data_text = "\n".join(data_lines)

        if llm and llm.available_providers():
            try:
                resp = await llm.complete(
                    messages=[
                        LLMMessage(role="system", content=_DAILY_SYSTEM),
                        LLMMessage(role="user", content=data_text),
                    ],
                    task_type="report", max_tokens=800, temperature=0.3,
                )
                report = json.loads(resp.content)
            except Exception as e:
                logger.warning("LLM daily report failed: %s", e)
                report = self._fallback_daily(recent, dir_activity)
        else:
            report = self._fallback_daily(recent, dir_activity)

        await memory.store_knowledge(
            kid=f"daily_report_{datetime.now().strftime('%Y%m%d')}",
            category="daily_report",
            content=json.dumps(report, ensure_ascii=False),
            metadata={"generated_at": time.time()},
        )
        return report

    def _fallback_daily(self, recent, dir_activity) -> dict:
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "summary": f"{len(recent)} files modified today",
            "files_changed": len(recent),
            "active_projects": [d["directory"] for d in dir_activity[:5]],
            "key_changes": [f["path"].split("/")[-1] for f in recent[:10]],
            "inferred_todos": [],
        }

    async def _generate_project_profile(self, context: dict[str, Any], project_dir: str | None) -> dict:
        memory = context["memory"]
        llm = context.get("llm")

        if not project_dir:
            dir_activity = await memory.get_directory_activity(depth=2)
            if dir_activity:
                project_dir = dir_activity[0]["directory"]
            else:
                return {"error": "No project directory specified or detected"}

        results = await memory.search_files(project_dir, limit=50)

        file_types: dict[str, int] = {}
        file_list: list[str] = []
        for r in results:
            ft = r.get("file_type", "?")
            file_types[ft] = file_types.get(ft, 0) + 1
            file_list.append(r["path"])

        profile = {
            "project_dir": project_dir,
            "file_count": len(results),
            "file_types": file_types,
            "key_files": file_list[:20],
        }

        if llm and llm.available_providers():
            try:
                resp = await llm.complete(
                    messages=[
                        LLMMessage(role="system", content="Analyze this project structure and output a JSON profile with keys: tech_stack, description, entry_points, dependencies. Output ONLY JSON."),
                        LLMMessage(role="user", content=json.dumps(profile, indent=2)),
                    ],
                    task_type="report", max_tokens=600, temperature=0.3,
                )
                llm_profile = json.loads(resp.content)
                profile.update(llm_profile)
            except Exception as e:
                logger.warning("LLM project profile failed: %s", e)

        await memory.store_knowledge(
            kid=f"project_profile_{project_dir.replace('/', '_')[:50]}",
            category="project_profile",
            content=json.dumps(profile, ensure_ascii=False),
            source_path=project_dir,
            metadata={"generated_at": time.time()},
        )
        return profile

    async def _generate_context_brief(self, context: dict[str, Any]) -> dict:
        memory = context["memory"]
        llm = context.get("llm")

        # Gather all available intelligence
        behavior = await memory.query_knowledge(category="behavior_insight", limit=1)
        profile = await memory.query_knowledge(category="user_profile", limit=1)
        recent = await memory.get_recently_modified_files(hours=48, limit=50)
        mem_stats = await memory.stats()

        data = {
            "recent_files": [f["path"] for f in recent[:20]],
            "behavior_insight": json.loads(behavior[0]["content"]) if behavior else {},
            "user_profile": json.loads(profile[0]["content"]) if profile else {},
            "index_stats": mem_stats,
        }

        if llm and llm.available_providers():
            try:
                resp = await llm.complete(
                    messages=[
                        LLMMessage(role="system", content=_BRIEF_SYSTEM),
                        LLMMessage(role="user", content=json.dumps(data, indent=2, default=str)),
                    ],
                    task_type="report", max_tokens=800, temperature=0.3,
                )
                brief = json.loads(resp.content)
            except Exception as e:
                logger.warning("LLM context brief failed: %s", e)
                brief = {"raw_data": data}
        else:
            brief = {
                "current_focus": data["behavior_insight"].get("focus_areas", ["unknown"]),
                "recent_activity": f"{len(recent)} files in last 48h",
                "key_files": data["recent_files"][:10],
                "user_preferences": data["user_profile"],
                "suggested_context": "",
            }

        await memory.store_knowledge(
            kid=f"context_brief_{int(time.time())}",
            category="context_brief",
            content=json.dumps(brief, ensure_ascii=False),
            metadata={"generated_at": time.time()},
        )
        return brief
