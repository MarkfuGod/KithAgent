"""
ProfileBuilderAgent — builds a persistent whole-person user profile.

Infers across ALL dimensions of the user's digital life:
  - Technical:  languages, frameworks, projects, coding style
  - Academic:   research areas, learning topics, reference materials
  - Personal:   document types, media, hobbies, digital organization
  - Behavioral: work hours, productivity patterns, tool usage

Stored in knowledge table under category "user_profile".
External agents fetch this on startup to personalize their behavior.
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

logger = logging.getLogger("agent_sys.agents.profile_builder")

_SYSTEM_PROMPT = """You are a user profile builder for AgentOS.
Given filesystem statistics, activity patterns, and file category breakdowns,
build a COMPREHENSIVE profile of this person — not just their coding habits.

Produce JSON:
{
  "identity": {
    "summary": "1-2 sentence description of who this person is",
    "roles": ["developer", "student", "researcher", etc.]
  },
  "technical": {
    "primary_languages": [{"language": "...", "file_count": N, "confidence": "high|medium|low"}],
    "frameworks": ["detected frameworks and libraries"],
    "tools": ["IDEs, CLI tools, etc."],
    "coding_style": {
      "naming_convention": "snake_case|camelCase|mixed",
      "project_structure": "description"
    }
  },
  "projects": [
    {"name": "...", "path": "...", "status": "active|maintained|archived", "category": "work|study|personal"}
  ],
  "interests": {
    "professional": ["work-related interests"],
    "academic": ["research/study topics inferred from files"],
    "personal": ["hobbies and personal interests inferred from non-work files"]
  },
  "digital_footprint": {
    "total_files": N,
    "content_mix": {"code": N, "documents": N, "images": N, "data": N, "other": N},
    "organization_style": "how they organize files and folders"
  },
  "work_patterns": {
    "most_active_hours": "e.g. late night, early morning",
    "most_active_days": "if detectable",
    "productivity_style": "deep focus, multitasker, etc."
  },
  "expertise_areas": ["ALL areas of expertise, not just coding"]
}
Output ONLY valid JSON."""


class ProfileBuilderAgent(BaseAgent):
    """Build a persistent whole-person user profile from indexed data."""
    name = "profile_builder"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        llm = context.get("llm")

        file_stats = await memory.get_file_modification_stats()
        dir_activity = await memory.get_directory_activity(depth=2)
        dir_breakdown = await memory.get_directory_breakdown(depth=2)
        recent = await memory.get_recently_modified_files(hours=168, limit=200)
        mem_stats = await memory.stats()
        projects = await memory.get_project_directories(min_files=3)

        doc_files = await memory.get_files_by_category("document", limit=30)
        img_files = await memory.get_files_by_category("image", limit=20)

        config_files = await memory.search_files("package.json", limit=10)
        config_files += await memory.search_files("requirements.txt", limit=10)
        config_files += await memory.search_files("Cargo.toml", limit=5)
        config_files += await memory.search_files("pyproject.toml", limit=10)

        data_summary = self._build_data_summary(
            file_stats, dir_activity, dir_breakdown,
            recent, config_files, mem_stats, projects,
            doc_files, img_files,
        )

        if llm and llm.available_providers():
            profile = await self._llm_profile(llm, data_summary)
        else:
            profile = self._rule_based_profile(
                file_stats, dir_activity, dir_breakdown, config_files, mem_stats, projects,
            )

        await memory.store_knowledge(
            knowledge_id="user_profile_current",
            category="user_profile",
            content=json.dumps(profile, ensure_ascii=False),
            metadata={"generated_at": time.time(), "indexed_files": mem_stats.get("indexed_files", 0)},
        )

        logger.info(
            "User profile built/updated: %d languages, %d projects, %d expertise areas",
            len(profile.get("technical", {}).get("primary_languages", profile.get("primary_languages", []))),
            len(profile.get("projects", [])),
            len(profile.get("expertise_areas", [])),
        )
        return profile

    def _build_data_summary(
        self, file_stats, dir_activity, dir_breakdown,
        recent, config_files, mem_stats, projects,
        doc_files, img_files,
    ) -> str:
        lines = [
            f"Total indexed files: {mem_stats.get('indexed_files', 0)}",
            f"Analysis time: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ]

        lines.append("\n=== File Type Distribution ===")
        for s in file_stats[:20]:
            lines.append(f"  {s['file_type']}: {s['file_count']} files, avg {s['avg_size']:.0f} bytes")

        lines.append(f"\n=== Directory Content Breakdown ({len(dir_breakdown)} dirs) ===")
        for d in dir_breakdown[:20]:
            lines.append(
                f"  ~/{d['directory']}: {d['total']} files "
                f"(code={d['code']}, doc={d['document']}, img={d['image']}, "
                f"data={d['data']}, other={d['other']})"
            )

        lines.append(f"\n=== Discovered Projects ({len(projects)} total) ===")
        for p in projects[:25]:
            lines.append(f"  [{p['marker']}] {p['directory']} ({p['file_count']} files)")

        lines.append("\n=== Detected Config/Project Files ===")
        for c in config_files[:20]:
            lines.append(f"  {c['path']}")

        if doc_files:
            lines.append(f"\n=== Documents ({len(doc_files)} samples) ===")
            for f in doc_files[:15]:
                name = Path(f["path"]).name
                lines.append(f"  {f['file_type']} {name} — {f['path']}")

        if img_files:
            lines.append(f"\n=== Images ({len(img_files)} samples) ===")
            for f in img_files[:10]:
                name = Path(f["path"]).name
                lines.append(f"  {f['file_type']} {name} — {f['path']}")

        lines.append(f"\n=== Recent Activity (last 7 days, {len(recent)} files) ===")
        hours_seen: dict[int, int] = {}
        for f in recent:
            if f.get("modified_at"):
                h = datetime.fromtimestamp(f["modified_at"]).hour
                hours_seen[h] = hours_seen.get(h, 0) + 1
        if hours_seen:
            sorted_hours = sorted(hours_seen.items(), key=lambda x: -x[1])
            lines.append(f"  Peak hours: {', '.join(f'{h}:00 ({c} files)' for h, c in sorted_hours[:8])}")

        lines.append("\n=== Top Active Directories ===")
        for d in dir_activity[:15]:
            lines.append(f"  {d['directory']}: {d['file_count']} files")

        return "\n".join(lines)

    async def _llm_profile(self, llm, data_summary: str) -> dict:
        try:
            resp = await llm.complete(
                messages=[
                    LLMMessage(role="system", content=_SYSTEM_PROMPT),
                    LLMMessage(role="user", content=f"Build a complete user profile from this data:\n\n{data_summary}"),
                ],
                task_type="profile", max_tokens=2000, temperature=0.3,
            )
            return json.loads(resp.content)
        except Exception as e:
            logger.warning("LLM profile build failed: %s", e)
            return {"error": str(e), "data_summary": data_summary[:500]}

    def _rule_based_profile(self, file_stats, dir_activity, dir_breakdown, config_files, mem_stats, projects) -> dict:
        ext_to_lang = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".go": "Go", ".rs": "Rust", ".sh": "Shell",
            ".swift": "Swift", ".java": "Java", ".c": "C", ".cpp": "C++",
        }

        code_total = sum(d.get("code", 0) for d in dir_breakdown)
        doc_total = sum(d.get("document", 0) for d in dir_breakdown)
        img_total = sum(d.get("image", 0) for d in dir_breakdown)
        data_total = sum(d.get("data", 0) for d in dir_breakdown)
        other_total = sum(d.get("other", 0) for d in dir_breakdown)

        languages = []
        for s in file_stats:
            lang = ext_to_lang.get(s["file_type"])
            if lang:
                languages.append({"language": lang, "file_count": s["file_count"], "confidence": "medium"})

        proj_list = [
            {"name": p["directory"].split("/")[-1], "path": p["directory"],
             "status": "active", "category": "work"}
            for p in projects[:15]
        ]

        return {
            "identity": {
                "summary": "Profile requires LLM for full analysis",
                "roles": ["developer"],
            },
            "technical": {
                "primary_languages": languages[:8],
                "frameworks": [],
                "tools": [],
                "coding_style": {"naming_convention": "unknown", "project_structure": "unknown"},
            },
            "projects": proj_list,
            "interests": {"professional": [], "academic": [], "personal": []},
            "digital_footprint": {
                "total_files": mem_stats.get("indexed_files", 0),
                "content_mix": {
                    "code": code_total, "documents": doc_total, "images": img_total,
                    "data": data_total, "other": other_total,
                },
                "organization_style": "unknown",
            },
            "work_patterns": {},
            "expertise_areas": [l["language"] for l in languages[:5]],
        }
