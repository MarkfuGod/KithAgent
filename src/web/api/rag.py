"""RAG dashboard endpoints: config, status, manual trigger, and debug logs."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import yaml
from aiohttp import web

from src.web._utils import DEFAULT_CONFIG_PATH, get_db

_LOG_PATH = Path.home() / ".agent_sys" / "logs" / "sysagent.log"


def _load_config() -> dict[str, Any]:
    if not DEFAULT_CONFIG_PATH.exists():
        return {}
    with open(DEFAULT_CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def _save_config(raw: dict[str, Any]) -> None:
    with open(DEFAULT_CONFIG_PATH, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _rag_config(raw: dict[str, Any]) -> dict[str, Any]:
    return (raw.get("memory", {}) or {}).get("rag", {}) or {}


def _db_stats(cfg: dict[str, Any]) -> dict[str, Any]:
    if not (Path.home() / ".agent_sys" / "memory.db").exists():
        return {"available": False}

    db = get_db()
    try:
        tables = {
            r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            ).fetchall()
        }
        if "document_chunks" not in tables:
            return {"available": True, "schema_ready": False}

        statuses = [
            s for s in (cfg.get("allowed_triage_statuses") or ["high", "medium"])
            if s in {"high", "medium"}
        ] or ["high", "medium"]
        placeholders = ",".join("?" for _ in statuses)
        max_bytes = int(cfg.get("max_file_size_mb", 5)) * 1024 * 1024
        pending = db.execute(
            f"""SELECT COUNT(*)
                FROM file_index fi
                WHERE fi.triage_status IN ({placeholders})
                  AND fi.size_bytes <= ?
                  AND NOT EXISTS (
                    SELECT 1 FROM document_chunks dc
                    WHERE dc.path = fi.path AND dc.file_hash = fi.hash
                  )""",
            (*statuses, max_bytes),
        ).fetchone()[0]

        total_chunks = db.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
        embedded_chunks = db.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE embedding IS NOT NULL AND length(embedding) > 0"
        ).fetchone()[0]
        files_indexed = db.execute(
            "SELECT COUNT(DISTINCT path) FROM document_chunks"
        ).fetchone()[0]
        last_chunk = db.execute(
            "SELECT MAX(created_at) FROM document_chunks"
        ).fetchone()[0]
        return {
            "available": True,
            "schema_ready": True,
            "pending_files": pending,
            "total_chunks": total_chunks,
            "embedded_chunks": embedded_chunks,
            "files_indexed": files_indexed,
            "last_chunk_at": last_chunk,
        }
    except sqlite3.Error as e:
        return {"available": True, "schema_ready": False, "error": str(e)}
    finally:
        db.close()


async def status(request: web.Request) -> web.Response:
    try:
        raw = _load_config()
        cfg = _rag_config(raw)
        return web.json_response({
            "config": cfg,
            "stats": _db_stats(cfg),
            "logs": _read_logs(limit=8),
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def config_save(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    try:
        raw = _load_config()
        memory = raw.setdefault("memory", {})
        cfg = dict(memory.get("rag", {}) or {})
        for key in (
            "enabled", "initial_delay_seconds", "batch_size",
            "embedding_batch_size", "time_budget_seconds",
            "chunk_size_chars", "chunk_overlap_chars", "max_file_size_mb",
            "fts_top_k", "vector_top_k", "assistant_top_k", "min_score",
        ):
            if key in body:
                cfg[key] = body[key]
        if "allowed_triage_statuses" in body:
            cfg["allowed_triage_statuses"] = [
                s for s in body["allowed_triage_statuses"]
                if s in {"high", "medium"}
            ] or ["high", "medium"]
        memory["rag"] = cfg
        _save_config(raw)
        return web.json_response({"success": True, "config": cfg})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def trigger(request: web.Request) -> web.Response:
    """Submit rag_indexer through the existing daemon trigger proxy."""
    from src.web.api.daemon import submit_agent

    try:
        body = await request.json()
    except Exception:
        body = {}
    input_data = body.get("input_data", {}) if isinstance(body, dict) else {}
    return await submit_agent("rag_indexer", input_data)


async def debug_search(request: web.Request) -> web.Response:
    q = request.query.get("q", "").strip()
    if len(q) < 2:
        return web.json_response({"results": []})
    limit = max(1, min(int(request.query.get("limit", "6")), 20))
    try:
        from src.kernel.config import load_config
        from src.memory.store import MemoryStore

        cfg = load_config().memory
        store = MemoryStore(cfg)
        await store.initialize()
        try:
            rag = cfg.rag
            results = await store.hybrid_search_chunks(
                q,
                limit=limit,
                fts_limit=rag.fts_top_k,
                vector_limit=rag.vector_top_k,
                min_score=rag.min_score,
                allowed_triage_statuses=rag.allowed_triage_statuses,
            )
            return web.json_response({"results": results})
        finally:
            await store.stop()
    except Exception as e:
        return web.json_response({"error": str(e), "results": []}, status=500)


async def logs(request: web.Request) -> web.Response:
    limit = max(1, min(int(request.query.get("limit", "80")), 300))
    return web.json_response({"logs": _read_logs(limit=limit)})


def _read_logs(limit: int = 80) -> list[str]:
    if not _LOG_PATH.exists():
        return []
    try:
        lines = _LOG_PATH.read_text(errors="replace").splitlines()
    except Exception:
        return []
    needles = ("rag", "RAG", "document_chunks", "chunk", "hybrid")
    filtered = [line for line in lines if any(n in line for n in needles)]
    return filtered[-limit:]
