"""Background chunk indexer for hybrid RAG."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from src.agents.base import AgentTask, BaseAgent
from src.extractors import encode_image_base64, extract_content, render_pdf_page_base64
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
                if not extracted:
                    skipped += 1
                    continue
                if extracted.get("type") == "text":
                    content = str(extracted.get("content") or "")
                    chunks = chunk_text(
                        content,
                        path=path,
                        chunk_size_chars=chunk_size,
                        chunk_overlap_chars=chunk_overlap,
                    )
                    for chunk in chunks:
                        metadata = dict(chunk.get("metadata") or {})
                        metadata.setdefault("modality", "text")
                        metadata.setdefault("source_kind", "text")
                        chunk["metadata"] = metadata
                elif extracted.get("type") == "image":
                    chunks = self._build_media_chunks(path, file_info, extracted)
                else:
                    skipped += 1
                    continue
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

    def _build_media_chunks(
        self,
        path: str,
        file_info: dict[str, Any],
        extracted: dict[str, Any],
    ) -> list[dict[str, Any]]:
        summary = str(file_info.get("semantic_summary") or "").strip()
        file_type = str(file_info.get("file_type") or Path(path).suffix.lower())
        filename = Path(path).name
        content = summary
        if not content:
            label = "PDF page image" if file_type == ".pdf" else "Image"
            content = f"{label}: {filename}\nPath: {path}"

        extracted_meta = extracted.get("metadata") if isinstance(extracted.get("metadata"), dict) else {}
        source_kind = str(extracted_meta.get("source_kind") or ("scanned_pdf" if file_type == ".pdf" else "image"))
        metadata: dict[str, Any] = {
            "modality": "image",
            "source_kind": source_kind,
            "file_type": file_type,
            "path_name": filename,
            "preview_available": file_type != ".pdf",
            "embedding_input": "image" if file_type != ".pdf" else "pdf_page_image",
        }
        if "page" in extracted_meta:
            metadata["page"] = extracted_meta["page"]
            metadata["preview_available"] = True
        return [{
            "chunk_index": 0,
            "start_line": None,
            "end_line": None,
            "content": content,
            "metadata": metadata,
        }]

    async def _compute_pending_chunk_embeddings(self, memory, *, limit: int, deadline: float) -> int:
        if limit <= 0:
            return 0
        if time.time() >= deadline:
            return 0
        try:
            from src.memory.embeddings import embed_items, get_provider_info, is_available
            if not is_available():
                return 0
        except Exception:
            return 0

        model_name = str(get_provider_info().get("model", "unknown"))
        chunks = await memory.get_chunks_needing_embedding(limit=limit, embedding_model=model_name)
        if not chunks:
            return 0
        inputs = [self._embedding_input_for_chunk(chunk) for chunk in chunks]
        embeddings = embed_items(inputs)
        if not embeddings or len(embeddings) != len(chunks):
            return 0
        await memory.batch_update_chunk_embeddings([
            (chunk["id"], emb, model_name)
            for chunk, emb in zip(chunks, embeddings)
        ])
        logger.info("Computed %d RAG chunk embeddings", len(embeddings))
        return len(embeddings)

    def _embedding_input_for_chunk(self, chunk: dict[str, Any]) -> dict[str, Any]:
        text = str(chunk.get("content") or "")
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        if metadata.get("modality") != "image":
            return {"text": text}

        path = str(chunk.get("path") or "")
        image_data = None
        if metadata.get("source_kind") == "scanned_pdf":
            page = int(metadata.get("page") or 1)
            image_data = render_pdf_page_base64(path, page_number=max(page - 1, 0))
        else:
            image_data = encode_image_base64(path)

        item: dict[str, Any] = {"text": text}
        if image_data:
            item["image"] = image_data
        return item
