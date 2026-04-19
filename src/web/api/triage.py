"""
Triage dashboard endpoints.

`/api/triage`                     — overall distribution + top directories
`/api/triage/skipped-directories` — NEW: "which directories did the LLM
                                    (or rules) end up skipping, and how
                                    many files in each?" This is the
                                    visibility the user asked for when
                                    we moved semantic noise out of the
                                    watcher and into the triage layer.
"""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from src.web._utils import get_db


def _has_triage_column(db) -> bool:
    cols = {r[1] for r in db.execute("PRAGMA table_info(file_index)").fetchall()}
    return "triage_status" in cols


async def triage(request: web.Request) -> web.Response:
    db = get_db()
    try:
        if not _has_triage_column(db):
            return web.json_response({"available": False})

        stats = db.execute(
            """SELECT
                 CASE WHEN triage_status = '' OR triage_status IS NULL THEN 'untriaged'
                      ELSE triage_status END as status,
                 COUNT(*) as cnt
               FROM file_index GROUP BY status ORDER BY cnt DESC"""
        ).fetchall()

        by_dir = db.execute(
            """SELECT triage_status, COUNT(*) as cnt,
                      SUBSTR(path, 1, INSTR(SUBSTR(path, 2), '/') + 1) as top_dir
               FROM file_index
               WHERE triage_status != '' AND triage_status IS NOT NULL
               GROUP BY triage_status, top_dir
               ORDER BY cnt DESC LIMIT 30"""
        ).fetchall()

        return web.json_response({
            "available": True,
            "distribution": {r[0]: r[1] for r in stats},
            "by_directory": [
                {"status": r[0], "count": r[1], "directory": r[2]}
                for r in by_dir
            ],
        })
    finally:
        db.close()


async def skipped_directories(request: web.Request) -> web.Response:
    """Rank directories by how many files triage has marked 'skip'.

    Groups by a configurable depth (default 3 path components under $HOME)
    so bulk-skipped trees like `.cursor/extensions/<plugin>/` collapse into
    a single row instead of drowning the view.
    """
    depth = max(1, min(int(request.query.get("depth", "3")), 6))
    limit = max(1, min(int(request.query.get("limit", "40")), 200))

    db = get_db()
    try:
        if not _has_triage_column(db):
            return web.json_response({"available": False})

        rows = db.execute(
            """SELECT path, size_bytes
               FROM file_index
               WHERE triage_status = 'skip'"""
        ).fetchall()
    finally:
        db.close()

    home = str(Path.home())
    buckets: dict[str, dict] = {}
    for path, size in rows:
        rel = path[len(home):] if path.startswith(home) else path
        parts = rel.strip("/").split("/")
        key = "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1]) or "~"
        display = f"~/{key}" if key != "~" else "~"

        entry = buckets.setdefault(display, {"directory": display, "count": 0, "total_size": 0})
        entry["count"] += 1
        entry["total_size"] += size or 0

    ranked = sorted(buckets.values(), key=lambda x: x["count"], reverse=True)[:limit]

    return web.json_response({
        "available": True,
        "depth": depth,
        "directories": ranked,
        "total_skipped": sum(b["count"] for b in buckets.values()),
    })
