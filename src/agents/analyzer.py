"""
BehaviorAnalyzerAgent — holistic user behavior analysis.

Analyzes the user's ENTIRE digital life across three dimensions:
  - Work:          code projects, technical docs, configs, APIs
  - Study/Learning: tutorials, course materials, research papers, notes
  - Life:          personal docs, photos, downloads, media, hobbies

Results are stored in the knowledge table under category "behavior_insight".
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

logger = logging.getLogger("agent_sys.agents.analyzer")

_SYSTEM_PROMPT = """You are a holistic behavior analysis engine for AgentOS.
You are analyzing the user's ENTIRE computer — all indexed files across their home directory.
Your job is to understand this person's FULL digital life, NOT just their coding projects.

Analyze across THREE dimensions:

1. WORK — active projects, languages, tools, professional patterns
2. STUDY / LEARNING — tutorials, courses, research papers, practice repos, reference docs
3. PERSONAL LIFE — photos, personal documents (resumes, finance, travel), hobbies, entertainment

Given the aggregated filesystem data, produce a structured JSON analysis:
{
  "dimensions": {
    "work": {
      "projects": [
        {"name": "...", "path": "/...", "status": "active|maintained|archived",
         "languages": ["..."], "description": "..."}
      ],
      "primary_skills": ["Python backend", "iOS dev", "LLM/agent systems"],
      "tools": ["Cursor", "npm", "pip"],
      "current_focus": "what they are actively working on RIGHT NOW"
    },
    "study": {
      "topics": ["reinforcement learning", "manim/math visualization", "..."],
      "resources": ["course materials found", "reference repos", "tutorial projects"],
      "learning_stage": "description of what they're currently learning vs. mastered"
    },
    "personal": {
      "documents": "types of personal docs found (resumes, notes, finance, etc.)",
      "media": "photos, images, design files — what do they suggest?",
      "hobbies": ["inferred hobbies from non-work files"],
      "digital_organization": "how tidy/messy, naming patterns, folder structure"
    }
  },
  "file_landscape": {
    "total_files": N,
    "by_category": {"code": N, "documents": N, "images": N, "data": N, "other": N},
    "top_directories": [{"path": "...", "files": N, "dominant_type": "code|doc|image"}]
  },
  "personality_profile": "2-3 sentence summary of this person based on their entire digital footprint",
  "work_patterns": {
    "active_hours": "when they work",
    "style": "how they work (plan-driven, exploratory, etc.)",
    "organization": "how they organize things"
  },
  "recommendations": ["actionable suggestions across all dimensions"]
}

CRITICAL: Do NOT focus only on code. Look at documents, images, folder names, download
patterns, personal files — these reveal the WHOLE person. A PDF about machine learning
is study material. A folder named 'travel' suggests a hobby. Infer broadly.
Output ONLY valid JSON, no markdown fences."""


class BehaviorAnalyzerAgent(BaseAgent):
    """Analyze the full indexed filesystem to understand user behavior holistically."""
    name = "behavior_analyzer"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        llm = context.get("llm")

        hours = task.input_data.get("hours", 168)

        file_stats = await memory.get_file_modification_stats()
        mem_stats = await memory.stats()

        projects = await memory.get_project_directories(min_files=3)
        dir_activity = await memory.get_directory_activity(depth=3)
        dir_breakdown = await memory.get_directory_breakdown(depth=2)

        recent_files = await memory.get_recently_modified_files(hours=hours, limit=200)
        recent_short = await memory.get_recently_modified_files(hours=6, limit=50)

        doc_files = await memory.get_files_by_category("document", limit=40)
        img_files = await memory.get_files_by_category("image", limit=40)

        project_summaries = await memory.query_knowledge(category="project_summary", limit=50)

        data_summary = self._build_data_summary(
            file_stats, projects, dir_activity, dir_breakdown,
            recent_files, recent_short, doc_files, img_files,
            project_summaries, mem_stats,
        )

        if llm and llm.available_providers():
            analysis = await self._llm_analysis(llm, data_summary)
        else:
            analysis = self._rule_based_analysis(
                file_stats, projects, dir_activity, dir_breakdown, recent_files,
            )

        await memory.store_knowledge(
            kid=f"behavior_insight_{int(time.time())}",
            category="behavior_insight",
            content=json.dumps(analysis, ensure_ascii=False),
            metadata={"generated_at": time.time(), "hours_analyzed": hours},
        )

        logger.info(
            "Behavior analysis complete: %d file types, %d projects, %d recent files, %d active dirs",
            len(file_stats), len(projects), len(recent_files), len(dir_activity),
        )
        return analysis

    def _build_data_summary(
        self, file_stats, projects, dir_activity, dir_breakdown,
        recent_files, recent_short, doc_files, img_files,
        project_summaries, mem_stats,
    ) -> str:
        lines = [
            "=== System Overview ===",
            f"  Total indexed files: {mem_stats.get('indexed_files', 0)}",
            f"  Knowledge entries: {mem_stats.get('knowledge_entries', 0)}",
            f"  Analysis time: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ]

        lines.append("\n=== File Type Distribution (entire system) ===")
        for s in file_stats[:20]:
            lines.append(f"  {s['file_type']}: {s['file_count']} files, avg {s['avg_size']:.0f} bytes")

        lines.append(f"\n=== Directory Breakdown — content categories ({len(dir_breakdown)} dirs) ===")
        for d in dir_breakdown[:25]:
            lines.append(
                f"  ~/{d['directory']}: {d['total']} files "
                f"(code={d['code']}, doc={d['document']}, img={d['image']}, data={d['data']}, other={d['other']})"
            )

        lines.append(f"\n=== Discovered Projects ({len(projects)} total) ===")
        for p in projects[:30]:
            lines.append(f"  [{p['marker']}] {p['directory']} ({p['file_count']} files)")

        if project_summaries:
            lines.append("\n=== Project Summaries (from prior analysis) ===")
            for ps in project_summaries[:15]:
                try:
                    data = json.loads(ps["content"])
                    lines.append(f"  {data.get('directory', '?')}: {data.get('summary', '')[:200]}")
                except (json.JSONDecodeError, KeyError):
                    pass

        if doc_files:
            lines.append(f"\n=== Documents (PDF, Word, etc.) — {len(doc_files)} samples ===")
            for f in doc_files[:25]:
                name = Path(f["path"]).name
                size_kb = (f["size_bytes"] or 0) // 1024
                summary = f["semantic_summary"][:80] if f.get("semantic_summary") else ""
                lines.append(f"  {f['file_type']} {name} ({size_kb}KB) {f['path']}")
                if summary:
                    lines.append(f"    → {summary}")

        if img_files:
            lines.append(f"\n=== Images — {len(img_files)} samples ===")
            for f in img_files[:20]:
                name = Path(f["path"]).name
                size_kb = (f["size_bytes"] or 0) // 1024
                summary = f["semantic_summary"][:80] if f.get("semantic_summary") else ""
                lines.append(f"  {f['file_type']} {name} ({size_kb}KB) — {f['path']}")
                if summary:
                    lines.append(f"    → {summary}")

        lines.append("\n=== Most Active Directories ===")
        for d in dir_activity[:15]:
            lines.append(f"  {d['directory']}: {d['file_count']} recently modified files")

        lines.append(f"\n=== Current Focus (last 6 hours, {len(recent_short)} files) ===")
        for f in recent_short[:20]:
            ts = datetime.fromtimestamp(f["modified_at"]).strftime("%H:%M") if f["modified_at"] else "?"
            lines.append(f"  [{ts}] {f['file_type']}  {f['path']}")

        lines.append(f"\n=== Recent Activity (last 7 days, {len(recent_files)} files) ===")
        for f in recent_files[:30]:
            ts = datetime.fromtimestamp(f["modified_at"]).strftime("%Y-%m-%d %H:%M") if f["modified_at"] else "?"
            lines.append(f"  [{ts}] {f['file_type']}  {f['path']}")

        return "\n".join(lines)

    async def _llm_analysis(self, llm, data_summary: str) -> dict:
        try:
            response = await llm.complete(
                messages=[
                    LLMMessage(role="system", content=_SYSTEM_PROMPT),
                    LLMMessage(role="user", content=f"Analyze this person's complete digital life:\n\n{data_summary}"),
                ],
                task_type="analyze",
                max_tokens=3000,
                temperature=0.3,
            )
            return json.loads(response.content)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("LLM analysis failed, falling back to rules: %s", e)
            return {"error": str(e), "raw": data_summary[:500]}

    def _rule_based_analysis(self, file_stats, projects, dir_activity, dir_breakdown, recent_files) -> dict:
        """Fallback when no LLM is available."""
        ext_to_lang = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".go": "Go", ".rs": "Rust", ".sh": "Shell",
        }

        code_total = sum(d.get("code", 0) for d in dir_breakdown)
        doc_total = sum(d.get("document", 0) for d in dir_breakdown)
        img_total = sum(d.get("image", 0) for d in dir_breakdown)

        primary_skills = []
        for s in file_stats[:6]:
            lang = ext_to_lang.get(s["file_type"])
            if lang:
                primary_skills.append(lang)

        work_projects = []
        for p in projects[:20]:
            work_projects.append({
                "name": p["directory"].split("/")[-1],
                "path": p["directory"],
                "file_count": p["file_count"],
                "marker": p["marker"],
            })

        return {
            "dimensions": {
                "work": {
                    "projects": work_projects,
                    "primary_skills": primary_skills,
                    "current_focus": f"Active in {len(dir_activity)} directories recently",
                },
                "study": {"topics": [], "resources": [], "learning_stage": "requires LLM for inference"},
                "personal": {
                    "documents": f"{doc_total} document files found",
                    "media": f"{img_total} image files found",
                    "hobbies": [],
                },
            },
            "file_landscape": {
                "total_files": sum(s["file_count"] for s in file_stats),
                "by_category": {"code": code_total, "documents": doc_total, "images": img_total},
            },
            "personality_profile": "Detailed analysis requires LLM.",
            "recommendations": [],
        }
