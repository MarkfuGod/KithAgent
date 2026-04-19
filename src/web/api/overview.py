"""
Overview / files / recent / directory-tree / summary-progress endpoints.

These are all straight SQL aggregations against `file_index` — no syscalls,
no daemon dependency. The dashboard works even without the daemon running.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from pathlib import Path

from aiohttp import web

from src.web._utils import get_db


async def overview(request: web.Request) -> web.Response:
    db = get_db()
    try:
        total_files = db.execute("SELECT COUNT(*) FROM file_index").fetchone()[0]
        summarized = db.execute(
            "SELECT COUNT(*) FROM file_index "
            "WHERE semantic_summary != '' AND semantic_summary IS NOT NULL"
        ).fetchone()[0]
        knowledge_count = db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]

        type_dist = db.execute(
            "SELECT file_type, COUNT(*) as cnt FROM file_index "
            "GROUP BY file_type ORDER BY cnt DESC"
        ).fetchall()

        priority_dist = db.execute(
            "SELECT priority, COUNT(*) as cnt FROM file_index "
            "GROUP BY priority ORDER BY priority"
        ).fetchall()

        total_size = db.execute("SELECT SUM(size_bytes) FROM file_index").fetchone()[0] or 0

        recent_24h = db.execute(
            "SELECT COUNT(*) FROM file_index WHERE modified_at > ?",
            (time.time() - 86400,),
        ).fetchone()[0]

        daemon_status = _probe_daemon()

        return web.json_response({
            "total_files": total_files,
            "summarized_files": summarized,
            "unsummarized_files": total_files - summarized,
            "knowledge_entries": knowledge_count,
            "total_size_bytes": total_size,
            "recent_24h_modified": recent_24h,
            "file_type_distribution": [
                {"type": r[0] or "unknown", "count": r[1]} for r in type_dist
            ],
            "priority_distribution": [
                {"priority": r[0], "count": r[1]} for r in priority_dist
            ],
            "daemon": daemon_status,
        })
    finally:
        db.close()


def _probe_daemon() -> dict:
    """Cheap liveness probe — PID file + signal 0. No syscalls."""
    pid_file = Path("/tmp/agent_sys.pid")
    if not pid_file.exists():
        return {"running": False, "pid": None}
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return {"running": True, "pid": pid}
    except (ProcessLookupError, ValueError):
        return {"running": False, "pid": None}


async def directory_tree(request: web.Request) -> web.Response:
    depth = int(request.query.get("depth", "2"))
    db = get_db()
    try:
        rows = db.execute(
            "SELECT path, file_type, size_bytes FROM file_index"
        ).fetchall()

        dir_stats: dict[str, dict] = defaultdict(lambda: {
            "code": 0, "document": 0, "image": 0, "data": 0, "config": 0, "other": 0,
            "total": 0, "total_size": 0,
        })

        _code = {".py", ".js", ".ts", ".go", ".rs", ".sh", ".java", ".c", ".cpp", ".swift", ".kt", ".rb", ".php"}
        _doc = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".md", ".txt", ".rst", ".tex"}
        _img = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
        _data = {".csv", ".xml"}
        _config = {".json", ".yaml", ".yml", ".toml"}

        home = str(Path.home())
        for path, ftype, size in rows:
            rel = path[len(home):] if path.startswith(home) else path
            parts = rel.strip("/").split("/")
            dir_key = "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1]) or "~"

            s = dir_stats[dir_key]
            s["total"] += 1
            s["total_size"] += (size or 0)

            ext = (ftype or "").lower()
            if ext in _code:
                s["code"] += 1
            elif ext in _doc:
                s["document"] += 1
            elif ext in _img:
                s["image"] += 1
            elif ext in _data:
                s["data"] += 1
            elif ext in _config:
                s["config"] += 1
            else:
                s["other"] += 1

        result = []
        for d, s in sorted(dir_stats.items(), key=lambda x: x[1]["total"], reverse=True):
            s["directory"] = d
            result.append(s)

        return web.json_response(result[:80])
    finally:
        db.close()


async def recent_files(request: web.Request) -> web.Response:
    hours = float(request.query.get("hours", "24"))
    limit = int(request.query.get("limit", "100"))
    db = get_db()
    try:
        cutoff = time.time() - (hours * 3600)
        rows = db.execute(
            """SELECT path, file_type, modified_at, size_bytes, priority,
                      COALESCE(semantic_summary, '') as semantic_summary
               FROM file_index WHERE modified_at > ?
               ORDER BY modified_at DESC LIMIT ?""",
            (cutoff, limit),
        ).fetchall()

        home = str(Path.home())
        return web.json_response([
            {
                "path": r[0].replace(home, "~"),
                "full_path": r[0],
                "type": r[1], "modified_at": r[2],
                "size": r[3], "priority": r[4],
                "summary": r[5][:200] if r[5] else "",
            }
            for r in rows
        ])
    finally:
        db.close()


async def summary_progress(request: web.Request) -> web.Response:
    db = get_db()
    try:
        total = db.execute("SELECT COUNT(*) FROM file_index").fetchone()[0]
        done = db.execute(
            "SELECT COUNT(*) FROM file_index "
            "WHERE semantic_summary != '' AND semantic_summary IS NOT NULL"
        ).fetchone()[0]

        by_type = db.execute(
            """SELECT file_type,
                      COUNT(*) as total,
                      SUM(CASE WHEN semantic_summary != '' AND semantic_summary IS NOT NULL THEN 1 ELSE 0 END) as done
               FROM file_index
               GROUP BY file_type
               ORDER BY total DESC"""
        ).fetchall()

        return web.json_response({
            "total": total,
            "summarized": done,
            "pending": total - done,
            "percent": round(done / total * 100, 1) if total > 0 else 0,
            "by_type": [
                {"type": r[0] or "unknown", "total": r[1], "done": r[2],
                 "percent": round(r[2] / r[1] * 100, 1) if r[1] > 0 else 0}
                for r in by_type
            ],
        })
    finally:
        db.close()
