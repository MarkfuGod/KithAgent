"""
BehaviorAnalyzerAgent — analyzes user file access and modification patterns.

Produces insights like:
- Which directories are most active
- What file types are edited most
- Work time patterns
- Recent focus areas

Results are stored in the knowledge table under category "behavior_insight".
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

from src.agents.base import AgentTask, BaseAgent
from src.llm.base import LLMMessage

logger = logging.getLogger("agent_sys.agents.analyzer")

_SYSTEM_PROMPT = """You are a behavior analysis engine for AgentOS.
Given aggregated file system activity data, produce a structured JSON analysis with these keys:
{
  "active_projects": ["list of most active project directories"],
  "primary_languages": ["ranked list of languages by activity"],
  "focus_areas": ["what the user seems to be working on"],
  "work_patterns": "description of work time patterns if detectable",
  "recommendations": ["suggestions for the user or other agents"]
}
Output ONLY valid JSON, no markdown fences."""


class BehaviorAnalyzerAgent(BaseAgent):
    """Analyze file modification patterns to understand user behavior."""
    name = "behavior_analyzer"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        llm = context.get("llm")

        hours = task.input_data.get("hours", 168)  # default: last 7 days

        # Gather raw data
        file_stats = await memory.get_file_modification_stats()
        recent_files = await memory.get_recently_modified_files(hours=hours, limit=200)
        dir_activity = await memory.get_directory_activity(depth=3)

        # Build a data summary for the LLM (or pure rule-based if no LLM)
        data_summary = self._build_data_summary(file_stats, recent_files, dir_activity)

        if llm and llm.available_providers():
            analysis = await self._llm_analysis(llm, data_summary)
        else:
            analysis = self._rule_based_analysis(file_stats, recent_files, dir_activity)

        # Store the insight
        await memory.store_knowledge(
            kid=f"behavior_insight_{int(time.time())}",
            category="behavior_insight",
            content=json.dumps(analysis, ensure_ascii=False),
            metadata={"generated_at": time.time(), "hours_analyzed": hours},
        )

        logger.info("Behavior analysis complete: %d file types, %d recent files, %d active dirs",
                     len(file_stats), len(recent_files), len(dir_activity))
        return analysis

    def _build_data_summary(self, file_stats, recent_files, dir_activity) -> str:
        lines = ["=== File Type Distribution ==="]
        for s in file_stats[:15]:
            lines.append(f"  {s['file_type']}: {s['file_count']} files, avg {s['avg_size']:.0f} bytes")

        lines.append("\n=== Most Active Directories ===")
        for d in dir_activity[:15]:
            lines.append(f"  {d['directory']}: {d['file_count']} recently modified files")

        lines.append("\n=== Recently Modified Files (sample) ===")
        for f in recent_files[:30]:
            ts = datetime.fromtimestamp(f["modified_at"]).strftime("%Y-%m-%d %H:%M") if f["modified_at"] else "?"
            lines.append(f"  [{ts}] {f['file_type']}  {f['path']}")

        return "\n".join(lines)

    async def _llm_analysis(self, llm, data_summary: str) -> dict:
        try:
            response = await llm.complete(
                messages=[
                    LLMMessage(role="system", content=_SYSTEM_PROMPT),
                    LLMMessage(role="user", content=f"Analyze this file system activity data:\n\n{data_summary}"),
                ],
                task_type="analyze",
                max_tokens=1024,
                temperature=0.3,
            )
            return json.loads(response.content)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("LLM analysis failed, falling back to rules: %s", e)
            return {"error": str(e), "raw": data_summary[:500]}

    def _rule_based_analysis(self, file_stats, recent_files, dir_activity) -> dict:
        """Fallback when no LLM is available."""
        primary_langs = []
        ext_to_lang = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".go": "Go", ".rs": "Rust", ".sh": "Shell",
        }
        for s in file_stats[:5]:
            lang = ext_to_lang.get(s["file_type"], s["file_type"])
            primary_langs.append(lang)

        active_dirs = [d["directory"] for d in dir_activity[:5]]

        return {
            "active_projects": active_dirs,
            "primary_languages": primary_langs,
            "focus_areas": [f"Active in {len(dir_activity)} directories"],
            "work_patterns": "analysis requires LLM",
            "recommendations": [],
            "file_type_counts": {s["file_type"]: s["file_count"] for s in file_stats[:10]},
        }
