"""
ReportGeneratorAgent — produces holistic structured reports.

Covers three dimensions of the user's digital life:
  - Work:     code projects, tech changes, inferred dev TODOs
  - Study:    learning materials accessed, research patterns
  - Personal: documents, images, personal file activity

Report types:
  1. daily:   Full day review across all dimensions
  2. quick:   Lightweight status snapshot (runs frequently)
  3. project: Per-project deep dive
  4. brief:   Context briefing for a new agent session

Reports are stored in the knowledge table and served via syscalls.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from src.agents.base import AgentTask, BaseAgent
from src.llm.base import LLMMessage

logger = logging.getLogger("agent_sys.agents.reporter")

_DAILY_SYSTEM = """You are a daily report generator for AgentOS.
You are reporting on the user's ENTIRE digital day — not just coding.

Given today's file activity (code, documents, images, configs, etc.), produce
a holistic daily report in JSON:
{
  "date": "YYYY-MM-DD",
  "summary": "2-3 sentence overview of the whole day",
  "dimensions": {
    "work": {
      "files_changed": N,
      "active_projects": ["project names"],
      "key_changes": ["notable work-related changes"],
      "inferred_todos": ["things in progress or needing attention"]
    },
    "study": {
      "activity": "what learning/research activity was detected (if any)",
      "topics": ["topics studied or referenced"]
    },
    "personal": {
      "activity": "personal file activity (docs, photos, downloads, etc.)",
      "notable": ["anything interesting outside of work"]
    }
  },
  "highlights": ["top 3-5 highlights of the day across all dimensions"],
  "time_pattern": "when the user was active and what they did at different times"
}
Output ONLY valid JSON."""

_QUICK_SYSTEM = """You are a quick status reporter for AgentOS.
Given the user's recent activity (ALL file types — code, documents, images, etc.),
produce a concise quick report in JSON:
{
  "timestamp": "ISO datetime",
  "activity_level": "active|moderate|quiet",
  "files_modified": N,
  "breakdown": {"code": N, "documents": N, "images": N, "other": N},
  "active_areas": ["top directories or projects"],
  "current_focus": "inferred from recent activity across ALL file types",
  "notable": ["anything worth highlighting"]
}
Be concise. Output ONLY valid JSON."""

_BRIEF_SYSTEM = """You are a context briefing generator for AgentOS.
Given the user's recent activity, behavior insights, and profile, produce a
JSON context brief that captures the WHOLE person (not just coding):
{
  "who": "brief description of this person based on their digital footprint",
  "current_focus": "what they are doing right now",
  "recent_activity": {
    "work": "summary of recent work",
    "study": "recent learning/research",
    "personal": "recent personal file activity"
  },
  "key_files": ["most relevant files for current context"],
  "preferences": "known preferences, patterns, and style",
  "suggested_context": "anything else a new agent session should know"
}
Output ONLY valid JSON."""


class ReportGeneratorAgent(BaseAgent):
    """Generate structured reports: daily, quick, project, brief."""
    name = "report_generator"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        report_type = task.input_data.get("report_type", "daily")

        if report_type == "daily":
            return await self._generate_daily(context)
        elif report_type == "quick":
            return await self._generate_quick(context)
        elif report_type == "project":
            return await self._generate_project_profile(context, task.input_data.get("project_dir"))
        elif report_type == "brief":
            return await self._generate_context_brief(context)
        else:
            return {"error": f"Unknown report type: {report_type}"}

    async def _generate_quick(self, context: dict[str, Any]) -> dict:
        memory = context["memory"]
        llm = context.get("llm")

        recent = await memory.get_recently_modified_files(hours=1, limit=50)
        mod_rate = await memory.get_modification_rate(minutes=30)
        dir_activity = await memory.get_directory_activity(depth=2)

        code_ext = {".py", ".js", ".ts", ".go", ".rs", ".sh", ".java", ".c", ".cpp", ".swift"}
        doc_ext = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".md", ".txt"}
        img_ext = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

        breakdown = {"code": 0, "documents": 0, "images": 0, "other": 0}
        for f in recent:
            ext = (f.get("file_type") or "").lower()
            if ext in code_ext:
                breakdown["code"] += 1
            elif ext in doc_ext:
                breakdown["documents"] += 1
            elif ext in img_ext:
                breakdown["images"] += 1
            else:
                breakdown["other"] += 1

        data_lines = [
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"Files modified in last hour: {len(recent)}",
            f"Files modified in last 30min: {mod_rate}",
            f"Breakdown: code={breakdown['code']}, docs={breakdown['documents']}, "
            f"images={breakdown['images']}, other={breakdown['other']}",
            "",
            "Recent files (last hour):",
        ]
        for f in recent[:20]:
            ts = datetime.fromtimestamp(f["modified_at"]).strftime("%H:%M") if f["modified_at"] else "?"
            name = Path(f["path"]).name
            data_lines.append(f"  [{ts}] {f['file_type']}  {name} — {f['path']}")

        data_lines.append("\nTop directories:")
        for d in dir_activity[:8]:
            data_lines.append(f"  {d['directory']}: {d['file_count']} files")

        data_text = "\n".join(data_lines)

        if llm and llm.available_providers():
            try:
                resp = await llm.complete(
                    messages=[
                        LLMMessage(role="system", content=_QUICK_SYSTEM),
                        LLMMessage(role="user", content=data_text),
                    ],
                    task_type="report", max_tokens=500, temperature=0.3,
                )
                report = json.loads(resp.content)
            except Exception as e:
                logger.warning("LLM quick report failed: %s", e)
                report = self._fallback_quick(recent, dir_activity, mod_rate, breakdown)
        else:
            report = self._fallback_quick(recent, dir_activity, mod_rate, breakdown)

        await memory.store_knowledge(
            kid=f"quick_report_{int(time.time())}",
            category="quick_report",
            content=json.dumps(report, ensure_ascii=False),
            metadata={"generated_at": time.time()},
        )
        return report

    def _fallback_quick(self, recent, dir_activity, mod_rate, breakdown) -> dict:
        level = "active" if mod_rate > 5 else ("moderate" if mod_rate > 0 else "quiet")
        return {
            "timestamp": datetime.now().isoformat(),
            "activity_level": level,
            "files_modified": len(recent),
            "breakdown": breakdown,
            "active_areas": [d["directory"] for d in dir_activity[:3]],
            "current_focus": recent[0]["path"].split("/")[-2] if recent else "unknown",
            "notable": [],
        }

    async def _generate_daily(self, context: dict[str, Any]) -> dict:
        memory = context["memory"]
        llm = context.get("llm")

        recent = await memory.get_recently_modified_files(hours=24, limit=150)
        dir_activity = await memory.get_directory_activity(depth=3)
        doc_files = await memory.get_files_by_category("document", limit=20)
        img_files = await memory.get_files_by_category("image", limit=20)
        dir_breakdown = await memory.get_directory_breakdown(depth=2)

        code_ext = {".py", ".js", ".ts", ".go", ".rs", ".sh", ".java", ".c", ".cpp", ".swift"}
        doc_ext = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".md", ".txt"}
        img_ext = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

        breakdown = {"code": 0, "documents": 0, "images": 0, "config_data": 0, "other": 0}
        for f in recent:
            ext = (f.get("file_type") or "").lower()
            if ext in code_ext:
                breakdown["code"] += 1
            elif ext in doc_ext:
                breakdown["documents"] += 1
            elif ext in img_ext:
                breakdown["images"] += 1
            elif ext in (".json", ".yaml", ".yml", ".toml", ".xml", ".csv"):
                breakdown["config_data"] += 1
            else:
                breakdown["other"] += 1

        data_lines = [
            f"Date: {datetime.now().strftime('%Y-%m-%d')}",
            f"Files modified in last 24h: {len(recent)}",
            f"Breakdown: {json.dumps(breakdown)}",
            "",
        ]

        # Time distribution
        hours_seen: dict[int, int] = {}
        for f in recent:
            if f.get("modified_at"):
                h = datetime.fromtimestamp(f["modified_at"]).hour
                hours_seen[h] = hours_seen.get(h, 0) + 1
        if hours_seen:
            sorted_hours = sorted(hours_seen.items(), key=lambda x: -x[1])
            data_lines.append(f"Active hours: {', '.join(f'{h}:00({c})' for h, c in sorted_hours[:8])}")

        data_lines.append("\n=== All modified files (by time) ===")
        for f in recent[:50]:
            ts = datetime.fromtimestamp(f["modified_at"]).strftime("%H:%M") if f["modified_at"] else "?"
            data_lines.append(f"  [{ts}] {f['file_type']}  {f['path']}")

        data_lines.append("\n=== Active directories ===")
        for d in dir_activity[:12]:
            data_lines.append(f"  {d['directory']}: {d['file_count']} files")

        data_lines.append("\n=== Directory content breakdown (top dirs) ===")
        for d in dir_breakdown[:10]:
            data_lines.append(
                f"  ~/{d['directory']}: code={d['code']} doc={d['document']} "
                f"img={d['image']} data={d['data']} other={d['other']}"
            )

        if doc_files:
            today = datetime.now().strftime("%Y-%m-%d")
            recent_docs = [f for f in doc_files if f.get("modified_at") and
                          datetime.fromtimestamp(f["modified_at"]).strftime("%Y-%m-%d") == today]
            if recent_docs:
                data_lines.append(f"\n=== Documents touched today ({len(recent_docs)}) ===")
                for f in recent_docs[:10]:
                    data_lines.append(f"  {f['file_type']} {Path(f['path']).name} — {f['path']}")

        if img_files:
            today = datetime.now().strftime("%Y-%m-%d")
            recent_imgs = [f for f in img_files if f.get("modified_at") and
                          datetime.fromtimestamp(f["modified_at"]).strftime("%Y-%m-%d") == today]
            if recent_imgs:
                data_lines.append(f"\n=== Images touched today ({len(recent_imgs)}) ===")
                for f in recent_imgs[:10]:
                    data_lines.append(f"  {f['file_type']} {Path(f['path']).name} — {f['path']}")

        data_text = "\n".join(data_lines)

        if llm and llm.available_providers():
            try:
                resp = await llm.complete(
                    messages=[
                        LLMMessage(role="system", content=_DAILY_SYSTEM),
                        LLMMessage(role="user", content=data_text),
                    ],
                    task_type="report", max_tokens=1500, temperature=0.3,
                )
                report = json.loads(resp.content)
            except Exception as e:
                logger.warning("LLM daily report failed: %s", e)
                report = self._fallback_daily(recent, dir_activity, breakdown)
        else:
            report = self._fallback_daily(recent, dir_activity, breakdown)

        await memory.store_knowledge(
            kid=f"daily_report_{datetime.now().strftime('%Y%m%d')}",
            category="daily_report",
            content=json.dumps(report, ensure_ascii=False),
            metadata={"generated_at": time.time()},
        )
        return report

    def _fallback_daily(self, recent, dir_activity, breakdown) -> dict:
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "summary": f"{len(recent)} files modified today",
            "dimensions": {
                "work": {
                    "files_changed": breakdown.get("code", 0),
                    "active_projects": [d["directory"] for d in dir_activity[:5]],
                    "key_changes": [],
                    "inferred_todos": [],
                },
                "study": {"activity": "requires LLM", "topics": []},
                "personal": {
                    "activity": f"{breakdown.get('documents', 0)} docs, {breakdown.get('images', 0)} images",
                    "notable": [],
                },
            },
            "highlights": [],
            "time_pattern": "",
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
                        LLMMessage(role="system", content=(
                            "Analyze this project structure and output a JSON profile with keys: "
                            "tech_stack, description, entry_points, dependencies, content_types "
                            "(what kinds of files — code, docs, images, data). Output ONLY JSON."
                        )),
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
                    task_type="report", max_tokens=1000, temperature=0.3,
                )
                brief = json.loads(resp.content)
            except Exception as e:
                logger.warning("LLM context brief failed: %s", e)
                brief = {"raw_data": data}
        else:
            brief = {
                "who": "analysis requires LLM",
                "current_focus": data["behavior_insight"].get("current_focus", "unknown"),
                "recent_activity": {"work": f"{len(recent)} files in 48h"},
                "key_files": data["recent_files"][:10],
                "preferences": data["user_profile"],
                "suggested_context": "",
            }

        await memory.store_knowledge(
            kid=f"context_brief_{int(time.time())}",
            category="context_brief",
            content=json.dumps(brief, ensure_ascii=False),
            metadata={"generated_at": time.time()},
        )
        return brief
