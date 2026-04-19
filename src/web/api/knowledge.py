"""
Knowledge-base + scheduling-history browser endpoints.

Both are thin wrappers over the `knowledge` table — scheduling decisions
are stored there under a dedicated category, so they share the same
"category + entries" shape.
"""

from __future__ import annotations

from aiohttp import web

from src.web._utils import get_db, safe_json


async def knowledge(request: web.Request) -> web.Response:
    category = request.query.get("category")
    limit = int(request.query.get("limit", "30"))
    db = get_db()
    try:
        if category:
            rows = db.execute(
                "SELECT id, category, content, source_path, created_at, updated_at "
                "FROM knowledge WHERE category = ? ORDER BY updated_at DESC LIMIT ?",
                (category, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, category, content, source_path, created_at, updated_at "
                "FROM knowledge ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        entries = [
            {
                "id": r[0], "category": r[1],
                "content": safe_json(r[2]),
                "source": r[3],
                "created_at": r[4], "updated_at": r[5],
            }
            for r in rows
        ]

        categories = db.execute(
            "SELECT category, COUNT(*) FROM knowledge "
            "GROUP BY category ORDER BY COUNT(*) DESC"
        ).fetchall()

        return web.json_response({
            "entries": entries,
            "categories": [{"name": c[0], "count": c[1]} for c in categories],
        })
    finally:
        db.close()


async def scheduling_history(request: web.Request) -> web.Response:
    """Recent scheduling decisions, stored under category='scheduling_decision'."""
    limit = int(request.query.get("limit", "20"))
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, content, created_at FROM knowledge "
            "WHERE category = 'scheduling_decision' "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

        return web.json_response([
            {"id": r[0], "decision": safe_json(r[1]), "time": r[2]}
            for r in rows
        ])
    finally:
        db.close()
