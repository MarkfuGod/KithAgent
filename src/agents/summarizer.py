"""
SummarizerAgent — generates semantic file summaries via LLM.

Two modes:
  - deep:  Read file content preview + LLM summarize (high quality, slower)
  - light: LLM summarize from metadata only (filename, type, size, mtime)
           — no file I/O, batches many files per LLM call

Runs incrementally with a time budget: processes files one by one,
persists each result immediately, and stops when the budget is exhausted.
The next cron cycle picks up where it left off.

After individual files are summarized, a hierarchical pass groups
summaries by project directory and produces project-level summaries
stored in the knowledge table.
"""

# TODO：这个总结agent是根据triage的优先级来吗，就是我觉得这里是并行处理任务好时机，比如并行好多subagent一块总结
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from src.agents.base import AgentTask, BaseAgent
from src.extractors import extract_content, is_image, is_document, is_plaintext
from src.llm.base import LLMMessage

logger = logging.getLogger("agent_sys.agents.summarizer")

_DEEP_SYSTEM = """You are a file analysis assistant for AgentOS.
Given a file's path, type, and first portion of its content, produce a concise
semantic summary (2-4 sentences) that captures:
- What this file does / its purpose
- Key entities (classes, functions, endpoints, config keys, etc.)
- Relationships to other parts of a project if apparent

Be factual and dense. No filler. Output ONLY the summary text."""

_LIGHT_SYSTEM = """You are a file analysis assistant for AgentOS.
Given a batch of file metadata (path, type, size, modification time), produce
a brief summary for EACH file based on what you can infer from the metadata alone
(filename conventions, directory structure, file type, size patterns).

Output a JSON array where each element is:
{"path": "<file path>", "summary": "<1 sentence inference>"}

Keep each summary SHORT (under 15 words). Be factual.
Output ONLY the raw JSON array, no markdown fences, no extra text."""

_HIERARCHICAL_SYSTEM = """You are a project analysis assistant for AgentOS.
Given a list of file summaries from a single project directory, produce a concise
project-level summary (3-5 sentences) that captures:
- What this project is / does
- Key technologies and frameworks
- Architecture patterns (if apparent)
- Current state (active development, stable, etc.)

Be factual and dense. Output ONLY the summary text."""

_VISION_SYSTEM = """You are a visual file analysis assistant for AgentOS.
Given an image file (screenshot, diagram, photo, design asset, etc.),
produce a concise semantic summary (2-4 sentences) that captures:
- What the image shows (UI screenshot, chart, photo, icon, diagram, etc.)
- Key visual content and any readable text
- Likely purpose in the context of the user's projects

Be factual and dense. No filler. Output ONLY the summary text."""

_DOC_SYSTEM = """You are a document analysis assistant for AgentOS.
Given the extracted text from a document (PDF, Word, etc.), produce a concise
semantic summary (2-4 sentences) that captures:
- What this document is about / its purpose
- Key topics, sections, or entities mentioned
- Document type (report, spec, resume, notes, etc.)

Be factual and dense. No filler. Output ONLY the summary text."""

_MAX_CONTENT_CHARS = 4000
_DEFAULT_TIME_BUDGET_SECONDS = 240
_LIGHT_BATCH_SIZE = 10


class SummarizerAgent(BaseAgent):
    """Use LLM to generate semantic summaries for indexed files."""
    name = "summarizer"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        memory = context["memory"]
        llm = context.get("llm")

        if not llm or not llm.available_providers():
            logger.warning("No LLM provider available — skipping summarization")
            return {"summarized": 0, "skipped_reason": "no_llm"}

        mode = task.input_data.get("mode", "deep")
        time_budget = task.input_data.get("time_budget", _DEFAULT_TIME_BUDGET_SECONDS)
        batch_size = task.input_data.get("batch_size", 30 if mode == "deep" else 200)
        event_bus = context.get("event_bus")

        start_time = time.time()

        if mode == "light":
            result = await self._run_light(memory, llm, batch_size, time_budget, start_time)
        else:
            result = await self._run_deep(memory, llm, batch_size, time_budget, start_time, event_bus=event_bus)

        # After individual summarization, attempt hierarchical project summaries
        elapsed = time.time() - start_time
        remaining = time_budget - elapsed
        if remaining > 30 and mode == "deep":
            hier_result = await self._build_hierarchical_summaries(memory, llm, remaining)
            result["hierarchical"] = hier_result

        # Compute embeddings for newly summarized files (if sentence-transformers available)
        embed_result = await self._compute_pending_embeddings(memory)
        if embed_result:
            result["embeddings_computed"] = embed_result

        return result

    async def _run_deep(self, memory, llm, batch_size, time_budget, start_time, event_bus=None) -> dict:
        """Deep mode: read file content + LLM summarize, one file at a time."""
        files = await memory.get_files_needing_summary(limit=batch_size)
        if not files:
            return {"summarized": 0, "mode": "deep", "message": "all files already summarized"}

        summarized = 0
        errors = 0
        vision_count = 0
        doc_count = 0
        summarized_files: list[dict] = []

        for f in files:
            if time.time() - start_time >= time_budget:
                logger.info("Time budget exhausted after %d files, will resume next cycle", summarized)
                break

            try:
                path = f["path"]
                ext = f.get("file_type", Path(path).suffix).lower()

                if is_image(ext):
                    result = await self._summarize_image(path, f, llm)
                    if result:
                        await memory.update_semantic_summary(path, f"[vision] {result}")
                        summarized += 1
                        vision_count += 1
                        summarized_files.append({"path": path, "type": "image", "summary": result[:120]})
                        if event_bus:
                            await event_bus.emit_dict("summarize.file_progress", {
                                "path": path, "type": "image",
                                "summarized": summarized, "total": len(files),
                                "preview": result[:120],
                            })
                    continue

                if is_document(ext):
                    result = await self._summarize_document(path, f, llm)
                    if result:
                        await memory.update_semantic_summary(path, f"[doc] {result}")
                        summarized += 1
                        doc_count += 1
                        summarized_files.append({"path": path, "type": "document", "summary": result[:120]})
                        if event_bus:
                            await event_bus.emit_dict("summarize.file_progress", {
                                "path": path, "type": "document",
                                "summarized": summarized, "total": len(files),
                                "preview": result[:120],
                            })
                    continue

                content_preview = self._read_preview(path)
                if not content_preview:
                    continue

                prompt = (
                    f"File: {path}\n"
                    f"Type: {ext}\n"
                    f"Size: {f['size_bytes']} bytes\n\n"
                    f"Content (first {_MAX_CONTENT_CHARS} chars):\n"
                    f"```\n{content_preview}\n```"
                )

                response = await llm.complete(
                    messages=[
                        LLMMessage(role="system", content=_DEEP_SYSTEM),
                        LLMMessage(role="user", content=prompt),
                    ],
                    task_type="summarize",
                    max_tokens=300,
                    temperature=0.2,
                )

                summary_text = response.content.strip()
                await memory.update_semantic_summary(path, summary_text)
                summarized += 1
                summarized_files.append({"path": path, "type": "code", "summary": summary_text[:120]})
                logger.debug("Summarized [deep]: %s", path)
                if event_bus:
                    await event_bus.emit_dict("summarize.file_progress", {
                        "path": path, "type": "code",
                        "summarized": summarized, "total": len(files),
                        "preview": summary_text[:120],
                    })

            except Exception as e:
                errors += 1
                logger.warning("Failed to summarize %s: %s", f.get("path"), e)

        elapsed = time.time() - start_time
        logger.info(
            "Summarizer [deep] complete: %d summarized (%d vision, %d doc), %d errors in %.1fs",
            summarized, vision_count, doc_count, errors, elapsed,
        )
        return {
            "summarized": summarized,
            "vision_files": vision_count,
            "document_files": doc_count,
            "errors": errors,
            "mode": "deep",
            "total_candidates": len(files),
            "elapsed_seconds": round(elapsed, 1),
            "files": summarized_files,
        }

    async def _summarize_image(self, path: str, file_info: dict, llm) -> str | None:
        """Use the vision model (qwen-vl-plus) to describe an image."""
        extracted = extract_content(path)
        if not extracted or extracted["type"] != "image":
            return None

        content = [
            {"type": "text", "text": (
                f"File: {path}\nSize: {file_info.get('size_bytes', 0)} bytes\n\n"
                "Describe what this image shows and its likely purpose."
            )},
            {"type": "image_url", "image_url": {"url": extracted["data_uri"]}},
        ]

        try:
            response = await llm.complete(
                messages=[
                    LLMMessage(role="system", content=_VISION_SYSTEM),
                    LLMMessage(role="user", content=content),
                ],
                task_type="summarize",
                is_vision=True,
                max_tokens=300,
                temperature=0.2,
            )
            logger.debug("Summarized [vision]: %s", path)
            return response.content.strip()
        except Exception as e:
            logger.warning("Vision summarization failed for %s: %s", path, e)
            return None

    async def _summarize_document(self, path: str, file_info: dict, llm) -> str | None:
        """Extract text from a document (PDF/Word) and summarize it."""
        extracted = extract_content(path)
        if not extracted:
            return None

        if extracted["type"] == "text":
            prompt = (
                f"Document: {path}\n"
                f"Type: {file_info.get('file_type', Path(path).suffix)}\n"
                f"Size: {file_info.get('size_bytes', 0)} bytes\n\n"
                f"Extracted text (first {_MAX_CONTENT_CHARS} chars):\n"
                f"```\n{extracted['content']}\n```"
            )
            response = await llm.complete(
                messages=[
                    LLMMessage(role="system", content=_DOC_SYSTEM),
                    LLMMessage(role="user", content=prompt),
                ],
                task_type="summarize",
                max_tokens=300,
                temperature=0.2,
            )
            logger.debug("Summarized [doc]: %s", path)
            return response.content.strip()

        if extracted["type"] == "image":
            # Scanned PDF — use vision model
            return await self._summarize_image(path, file_info, llm)

        return None

    async def _run_light(self, memory, llm, batch_size, time_budget, start_time) -> dict:
        """Light mode: LLM summarizes from metadata only, batched."""
        files = await memory.get_files_needing_summary(limit=batch_size)
        if not files:
            return {"summarized": 0, "mode": "light", "message": "all files already summarized"}

        summarized = 0
        errors = 0

        for i in range(0, len(files), _LIGHT_BATCH_SIZE):
            if time.time() - start_time >= time_budget:
                logger.info("Time budget exhausted after %d files, will resume next cycle", summarized)
                break

            batch = files[i:i + _LIGHT_BATCH_SIZE]
            metadata_lines = []
            for f in batch:
                mtime_str = datetime.fromtimestamp(f.get("modified_at", 0)).strftime("%Y-%m-%d %H:%M") if f.get("modified_at") else "unknown"
                metadata_lines.append({
                    "path": f["path"],
                    "type": f["file_type"],
                    "size_bytes": f["size_bytes"],
                    "modified": mtime_str,
                })

            try:
                response = await llm.complete(
                    messages=[
                        LLMMessage(role="system", content=_LIGHT_SYSTEM),
                        LLMMessage(role="user", content=json.dumps(metadata_lines, indent=1)),
                    ],
                    task_type="summarize",
                    max_tokens=2000,
                    temperature=0.2,
                )

                summaries = self._parse_json_lenient(response.content.strip())
                if summaries:
                    path_to_summary = {s["path"]: s["summary"] for s in summaries if "path" in s and "summary" in s}
                    for f in batch:
                        s = path_to_summary.get(f["path"])
                        if s:
                            await memory.update_semantic_summary(f["path"], f"[light] {s}")
                            summarized += 1
                else:
                    errors += 1
                    logger.warning("Light batch: LLM returned unparseable response, skipping batch")

            except Exception as e:
                errors += 1
                logger.warning("Light batch summarization failed: %s", e)

        elapsed = time.time() - start_time
        logger.info("Summarizer [light] complete: %d summarized, %d errors in %.1fs", summarized, errors, elapsed)
        return {
            "summarized": summarized,
            "errors": errors,
            "mode": "light",
            "total_candidates": len(files),
            "elapsed_seconds": round(elapsed, 1),
        }

    async def _build_hierarchical_summaries(self, memory, llm, remaining_budget: float) -> dict:
        """Group file summaries by project directory, produce project-level summaries."""
        start = time.time()
        projects = await memory.get_project_directories(min_files=3)
        if not projects:
            return {"projects_summarized": 0, "message": "no projects found"}

        summarized = 0
        for proj in projects:
            if time.time() - start >= remaining_budget:
                break

            directory = proj["directory"]

            # Skip if we already have a recent project summary
            existing = await memory.query_knowledge(category="project_summary", limit=100)
            already_done = any(
                e.get("source") == directory
                and (time.time() - json.loads(e.get("content", "{}")).get("generated_at", 0)) < 86400
                for e in existing
            )
            if already_done:
                continue

            files = await memory.get_files_by_directory(directory)
            file_summaries = [
                f"{f['path']}: {f['semantic_summary']}"
                for f in files if f.get("semantic_summary")
            ]

            if len(file_summaries) < 2:
                continue

            prompt = (
                f"Project directory: {directory}\n"
                f"File count: {proj['file_count']}\n"
                f"Project marker: {proj['marker']}\n\n"
                f"File summaries ({len(file_summaries)} files):\n"
                + "\n".join(file_summaries[:50])
            )

            try:
                response = await llm.complete(
                    messages=[
                        LLMMessage(role="system", content=_HIERARCHICAL_SYSTEM),
                        LLMMessage(role="user", content=prompt),
                    ],
                    task_type="summarize",
                    max_tokens=500,
                    temperature=0.3,
                )

                await memory.store_knowledge(
                    knowledge_id=f"project_summary_{directory.replace('/', '_')[:60]}",
                    category="project_summary",
                    content=json.dumps({
                        "directory": directory,
                        "summary": response.content.strip(),
                        "file_count": proj["file_count"],
                        "generated_at": time.time(),
                    }, ensure_ascii=False),
                    source_path=directory,
                    metadata={"file_count": proj["file_count"]},
                )
                summarized += 1
                logger.debug("Hierarchical summary: %s", directory)
            except Exception as e:
                logger.warning("Failed hierarchical summary for %s: %s", directory, e)

        logger.info("Hierarchical summarization: %d projects summarized", summarized)
        return {"projects_summarized": summarized, "total_projects": len(projects)}

    @staticmethod
    def _parse_json_lenient(text: str) -> list[dict] | None:
        """Try to parse a JSON array, with fallbacks for truncated LLM output."""
        # Strip markdown fences if present
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:])
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # Try to repair truncated JSON array: find the last complete object
        if text.startswith("["):
            for end_pos in range(len(text) - 1, 0, -1):
                if text[end_pos] == "}":
                    try:
                        result = json.loads(text[:end_pos + 1] + "]")
                        if isinstance(result, list):
                            return result
                    except json.JSONDecodeError:
                        continue

        return None

    @staticmethod
    async def _compute_pending_embeddings(memory, batch_size: int = 100) -> int:
        """Compute vector embeddings for files that have summaries but no embeddings."""
        try:
            from src.memory.embeddings import embed_texts, is_available
            if not is_available():
                return 0
        except ImportError:
            return 0

        files = await memory.get_files_needing_embedding(limit=batch_size)
        if not files:
            return 0

        texts = [f["semantic_summary"] for f in files]
        embeddings = embed_texts(texts)
        if not embeddings or len(embeddings) != len(files):
            return 0

        from src.memory.embeddings import get_provider_info
        model_name = get_provider_info().get("model", "unknown")
        updates = [
            (f["path"], emb, model_name)
            for f, emb in zip(files, embeddings)
        ]
        await memory.batch_update_embeddings(updates)
        logger.info("Computed %d embeddings for summarized files", len(updates))
        return len(updates)

    def _read_preview(self, path: str) -> str | None:
        try:
            p = Path(path)
            if not p.exists() or not p.is_file():
                return None
            text = p.read_text(errors="replace")
            return text[:_MAX_CONTENT_CHARS]
        except Exception:
            return None
