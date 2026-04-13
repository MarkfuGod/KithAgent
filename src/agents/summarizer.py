"""
SummarizerAgent — generates semantic file summaries via LLM.

Replaces the rule-based _extract_summary with real understanding.
Runs incrementally: only processes files whose hash changed or
that have no semantic_summary yet.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.agents.base import AgentTask, BaseAgent
from src.llm.base import LLMMessage

logger = logging.getLogger("agent_sys.agents.summarizer")

_SYSTEM_PROMPT = """You are a file analysis assistant for AgentOS.
Given a file's path, type, and first portion of its content, produce a concise
semantic summary (2-4 sentences) that captures:
- What this file does / its purpose
- Key entities (classes, functions, endpoints, config keys, etc.)
- Relationships to other parts of a project if apparent

Be factual and dense. No filler. Output ONLY the summary text."""

_MAX_CONTENT_CHARS = 4000


class SummarizerAgent(BaseAgent):
    """Use LLM to generate semantic summaries for indexed files."""
    name = "summarizer"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        llm = context.get("llm")

        if not llm or not llm.available_providers():
            logger.warning("No LLM provider available — skipping summarization")
            return {"summarized": 0, "skipped_reason": "no_llm"}

        batch_size = task.input_data.get("batch_size", 30)
        files = await memory.get_files_needing_summary(limit=batch_size)

        if not files:
            return {"summarized": 0, "message": "all files already summarized"}

        summarized = 0
        errors = 0
        for f in files:
            try:
                path = f["path"]
                content_preview = self._read_preview(path)
                if not content_preview:
                    continue

                prompt = (
                    f"File: {path}\n"
                    f"Type: {f['file_type']}\n"
                    f"Size: {f['size_bytes']} bytes\n\n"
                    f"Content (first {_MAX_CONTENT_CHARS} chars):\n"
                    f"```\n{content_preview}\n```"
                )

                response = await llm.complete(
                    messages=[
                        LLMMessage(role="system", content=_SYSTEM_PROMPT),
                        LLMMessage(role="user", content=prompt),
                    ],
                    task_type="summarize",
                    max_tokens=300,
                    temperature=0.2,
                )

                await memory.update_semantic_summary(path, response.content.strip())
                summarized += 1
                logger.debug("Summarized: %s", path)

            except Exception as e:
                errors += 1
                logger.warning("Failed to summarize %s: %s", f.get("path"), e)

        logger.info("Summarizer complete: %d summarized, %d errors", summarized, errors)
        return {"summarized": summarized, "errors": errors, "total_candidates": len(files)}

    def _read_preview(self, path: str) -> str | None:
        try:
            p = Path(path)
            if not p.exists() or not p.is_file():
                return None
            text = p.read_text(errors="replace")
            return text[:_MAX_CONTENT_CHARS]
        except Exception:
            return None
