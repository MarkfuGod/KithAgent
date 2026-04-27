"""Background chunk indexer for hybrid RAG."""

from __future__ import annotations

import logging
import time
from typing import Any

from src.agents.base import AgentTask, BaseAgent
from src.extractors import extract_content
from src.memory.chunking import chunk_text

logger = logging.getLogger("agent_sys.agents.rag_indexer")


class RagIndexerAgent(BaseAgent):
    """Incrementally builds local chunk + FTS + embedding indexes."""

    name = "rag_indexer"

    async def execute(self, task: AgentTask, context: dict[str, Any]) -> Any:
        kernel = context.get("kernel")
        memory = context["memory"]
        event_bus = context.get("event_bus")
        config = getattr(kernel, "config", None) if kernel else None
        memory_cfg = getattr(config, "memory", None)
        rag_cfg = getattr(memory_cfg, "rag", None)

        if rag_cfg and not getattr(rag_cfg, "enabled", True):
            return {"indexed_files": 0, "embedded_chunks": 0, "skipped_reason": "rag_disabled"}

        batch_size = int(task.input_data.get("batch_size", getattr(rag_cfg, "batch_size", 20)))
        embedding_batch_size = int(task.input_data.get(
            "embedding_batch_size",
            getattr(rag_cfg, "embedding_batch_size", 32),
        ))
        time_budget = float(task.input_data.get(
            "time_budget",
            getattr(rag_cfg, "time_budget_seconds", 90),
        ))
        chunk_size = int(task.input_data.get("chunk_size_chars", getattr(rag_cfg, "chunk_size_chars", 1600)))
        chunk_overlap = int(task.input_data.get("chunk_overlap_chars", getattr(rag_cfg, "chunk_overlap_chars", 250)))
        max_file_size_mb = int(task.input_data.get("max_file_size_mb", getattr(rag_cfg, "max_file_size_mb", 5)))
        allowed_statuses = task.input_data.get(
            "allowed_triage_statuses",
            getattr(rag_cfg, "allowed_triage_statuses", ["high", "medium"]),
        )

        started = time.time()
        deadline = started + time_budget
        max_file_size_bytes = max_file_size_mb * 1024 * 1024

        files = await memory.get_files_needing_rag_index(
            limit=batch_size,
            allowed_triage_statuses=allowed_statuses,
            max_file_size_bytes=max_file_size_bytes,
        )
        indexed_files = 0
        indexed_chunks = 0
        skipped = 0
        errors = 0

        if event_bus:
            await event_bus.emit_dict("rag_indexer.started", {
                "candidates": len(files),
                "batch_size": batch_size,
                "time_budget": time_budget,
            })
        logger.info(
            "RAG indexer started: candidates=%d batch_size=%d time_budget=%.1fs",
            len(files), batch_size, time_budget,
        )

        for file_info in files:
            if time.time() >= deadline:
                break
            path = file_info.get("path", "")
            try:
                extracted = extract_content(path, max_chars=max_file_size_bytes)
                if not extracted or extracted.get("type") != "text":
                    skipped += 1
                    continue
                content = str(extracted.get("content") or "")
                chunks = chunk_text(
                    content,
                    path=path,
                    chunk_size_chars=chunk_size,
                    chunk_overlap_chars=chunk_overlap,
                )
                if not chunks:
                    skipped += 1
                    continue
                count = await memory.upsert_document_chunks(path, file_info["hash"], chunks)
                indexed_files += 1
                indexed_chunks += count
            except Exception as e:
                errors += 1
                logger.warning("RAG chunk indexing failed for %s: %s", path, e)

        embedded_chunks = await self._compute_pending_chunk_embeddings(
            memory,
            limit=embedding_batch_size,
            deadline=deadline,
        )

        result = {
            "indexed_files": indexed_files,
            "indexed_chunks": indexed_chunks,
            "embedded_chunks": embedded_chunks,
            "skipped": skipped,
            "errors": errors,
            "candidates": len(files),
            "elapsed_seconds": round(time.time() - started, 1),
        }
        if event_bus:
            await event_bus.emit_dict("rag_indexer.completed", result)
        logger.info("RAG indexer completed: %s", result)
        return result

    async def _compute_pending_chunk_embeddings(self, memory, *, limit: int, deadline: float) -> int:
        if limit <= 0:
            return 0
        if time.time() >= deadline:
            return 0
        try:
            from src.memory.embeddings import embed_texts, get_provider_info, is_available
            if not is_available():
                return 0
        except Exception:
            return 0

        model_name = str(get_provider_info().get("model", "unknown"))
        chunks = await memory.get_chunks_needing_embedding(limit=limit, embedding_model=model_name)
        if not chunks:
            return 0
        texts = [c["content"] for c in chunks]
        embeddings = embed_texts(texts)
        if not embeddings or len(embeddings) != len(chunks):
            return 0
        await memory.batch_update_chunk_embeddings([
            (chunk["id"], emb, model_name)
            for chunk, emb in zip(chunks, embeddings)
        ])
        logger.info("Computed %d RAG chunk embeddings", len(embeddings))
        return len(embeddings)
