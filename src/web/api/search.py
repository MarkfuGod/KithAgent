"""
File search endpoint — vector (sentence-transformer) when available,
SQL LIKE fallback otherwise. Mirrors the heuristic used by FileSearchAgent.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from src.web._utils import get_db

logger = logging.getLogger("agent_sys.dashboard.search")


async def file_search(request: web.Request) -> web.Response:
    q = request.query.get("q", "")
    if not q or len(q) < 2:
        return web.json_response([])

    home = str(Path.home())
    # Keyword queries are short; natural-language ones tend to have 3+ tokens.
    use_vector = len(q.split()) >= 3

    if use_vector:
        vector_results = _try_vector_search(q, home)
        if vector_results is not None:
            return web.json_response(vector_results)

    return web.json_response(_keyword_search(q, home))


def _try_vector_search(q: str, home: str) -> list[dict] | None:
    """Return vector-search results, or None if embeddings are unavailable
    or returned nothing useful (caller falls back to LIKE search)."""
    try:
        from src.memory.embeddings import embed_text, is_available, cosine_similarity
    except Exception as e:
        logger.debug("Embeddings module not importable: %s", e)
        return None

    if not is_available():
        return None

    try:
        q_emb = embed_text(q)
        if not q_emb:
            return None

        db = get_db()
        try:
            rows = db.execute(
                """SELECT path, file_type, size_bytes, priority,
                          COALESCE(semantic_summary, '') as ss,
                          COALESCE(summary, '') as s, embedding
                   FROM file_index
                   WHERE embedding IS NOT NULL AND embedding != ''"""
            ).fetchall()
        finally:
            db.close()

        scored = []
        for r in rows:
            try:
                score = cosine_similarity(q_emb, r[6])
                scored.append((score, r))
            except Exception:
                continue
        scored.sort(key=lambda x: x[0], reverse=True)

        results = [
            {
                "path": r[0].replace(home, "~"),
                "type": r[1], "size": r[2], "priority": r[3],
                "summary": (r[4] or r[5] or "")[:200],
                "score": round(score, 4),
                "search_mode": "vector",
            }
            for score, r in scored[:50]
            if score > 0.25
        ]
        return results or None
    except Exception as e:
        logger.warning("Vector search failed, falling back to LIKE: %s", e)
        return None


def _keyword_search(q: str, home: str) -> list[dict]:
    db = get_db()
    try:
        rows = db.execute(
            """SELECT path, file_type, size_bytes, priority,
                      COALESCE(semantic_summary, '') as ss, COALESCE(summary, '') as s
               FROM file_index
               WHERE path LIKE ? OR summary LIKE ? OR semantic_summary LIKE ?
               ORDER BY priority ASC, modified_at DESC LIMIT 50""",
            (f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()

        return [
            {
                "path": r[0].replace(home, "~"),
                "type": r[1], "size": r[2], "priority": r[3],
                "summary": (r[4] or r[5] or "")[:200],
                "search_mode": "keyword",
            }
            for r in rows
        ]
    finally:
        db.close()
