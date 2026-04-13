"""
ProfileBuilderAgent — builds a persistent user profile from indexed data.

Infers:
  - Primary programming languages and frameworks
  - Project list and current focus
  - Coding style preferences (naming, structure)
  - Work time patterns

Stored in knowledge table under category "user_profile".
External agents fetch this on startup to personalize their behavior.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

from src.agents.base import AgentTask, BaseAgent
from src.llm.base import LLMMessage

logger = logging.getLogger("agent_sys.agents.profile_builder")

_SYSTEM_PROMPT = """You are a user profile builder for AgentOS.
Given file system statistics and activity patterns, build a comprehensive
user profile as JSON:
{
  "primary_languages": [{"language": "...", "file_count": N, "confidence": "high/medium/low"}],
  "frameworks": ["detected frameworks"],
  "projects": [{"name": "...", "path": "...", "status": "active/inactive"}],
  "coding_style": {
    "naming_convention": "snake_case / camelCase / mixed",
    "project_structure": "description of typical project layout"
  },
  "work_patterns": {
    "most_active_hours": "e.g. 9am-6pm or late night",
    "most_active_days": "e.g. weekdays"
  },
  "expertise_areas": ["inferred areas of expertise"],
  "tools": ["detected tools and editors"]
}
Output ONLY valid JSON."""


class ProfileBuilderAgent(BaseAgent):
    """Build a persistent user profile from indexed file data."""
    name = "profile_builder"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        llm = context.get("llm")

        # Gather comprehensive data
        file_stats = await memory.get_file_modification_stats()
        dir_activity = await memory.get_directory_activity(depth=2)
        recent = await memory.get_recently_modified_files(hours=168, limit=200)  # last 7 days
        mem_stats = await memory.stats()

        # Detect config files for framework/tool detection
        config_files = await memory.search_files("package.json", limit=10)
        config_files += await memory.search_files("requirements.txt", limit=10)
        config_files += await memory.search_files("Cargo.toml", limit=10)
        config_files += await memory.search_files("go.mod", limit=10)
        config_files += await memory.search_files("pyproject.toml", limit=10)

        data_summary = self._build_data_summary(file_stats, dir_activity, recent, config_files, mem_stats)

        if llm and llm.available_providers():
            profile = await self._llm_profile(llm, data_summary)
        else:
            profile = self._rule_based_profile(file_stats, dir_activity, config_files, mem_stats)

        await memory.store_knowledge(
            kid="user_profile_current",
            category="user_profile",
            content=json.dumps(profile, ensure_ascii=False),
            metadata={"generated_at": time.time(), "indexed_files": mem_stats.get("indexed_files", 0)},
        )

        logger.info("User profile built/updated: %d languages, %d projects detected",
                     len(profile.get("primary_languages", [])),
                     len(profile.get("projects", [])))
        return profile

    def _build_data_summary(self, file_stats, dir_activity, recent, config_files, mem_stats) -> str:
        lines = [f"Total indexed files: {mem_stats.get('indexed_files', 0)}", ""]

        lines.append("=== File Type Distribution ===")
        for s in file_stats[:15]:
            lines.append(f"  {s['file_type']}: {s['file_count']} files")

        lines.append("\n=== Top Directories ===")
        for d in dir_activity[:15]:
            lines.append(f"  {d['directory']}: {d['file_count']} files")

        lines.append("\n=== Detected Config/Project Files ===")
        for c in config_files[:20]:
            lines.append(f"  {c['path']}")

        lines.append(f"\n=== Recent Activity (last 7 days) ===")
        lines.append(f"  {len(recent)} files modified")
        hours_seen: dict[int, int] = {}
        for f in recent:
            if f.get("modified_at"):
                h = datetime.fromtimestamp(f["modified_at"]).hour
                hours_seen[h] = hours_seen.get(h, 0) + 1
        if hours_seen:
            sorted_hours = sorted(hours_seen.items(), key=lambda x: -x[1])
            lines.append(f"  Peak hours: {', '.join(f'{h}:00 ({c} files)' for h, c in sorted_hours[:5])}")

        return "\n".join(lines)

    async def _llm_profile(self, llm, data_summary: str) -> dict:
        try:
            resp = await llm.complete(
                messages=[
                    LLMMessage(role="system", content=_SYSTEM_PROMPT),
                    LLMMessage(role="user", content=f"Build a user profile from this data:\n\n{data_summary}"),
                ],
                task_type="profile", max_tokens=1200, temperature=0.3,
            )
            return json.loads(resp.content)
        except Exception as e:
            logger.warning("LLM profile build failed: %s", e)
            return {"error": str(e), "data_summary": data_summary[:500]}

    def _rule_based_profile(self, file_stats, dir_activity, config_files, mem_stats) -> dict:
        ext_to_lang = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".go": "Go", ".rs": "Rust", ".sh": "Shell", ".md": "Markdown",
            ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
        }

        languages = []
        for s in file_stats:
            lang = ext_to_lang.get(s["file_type"], s["file_type"])
            if s["file_type"] not in (".txt", ".md", ".json", ".yaml", ".yml", ".toml"):
                languages.append({"language": lang, "file_count": s["file_count"], "confidence": "medium"})

        projects = [{"name": d["directory"].split("/")[-1], "path": d["directory"], "status": "active"}
                    for d in dir_activity[:10]]

        return {
            "primary_languages": languages[:8],
            "frameworks": [],
            "projects": projects,
            "coding_style": {"naming_convention": "unknown", "project_structure": "unknown"},
            "work_patterns": {},
            "expertise_areas": [l["language"] for l in languages[:3]],
            "tools": [],
            "total_indexed_files": mem_stats.get("indexed_files", 0),
        }
