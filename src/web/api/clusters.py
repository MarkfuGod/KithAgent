"""File-cluster recommendation endpoints.

The dashboard uses these to show large file groups before spending summarize
tokens. A user can promote or exclude an entire cluster by path prefix.
"""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from src.web._utils import get_db


_CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml", ".xml"}
_DATA_EXTS = {".csv", ".tsv", ".jsonl"}


def _cluster_key(path: str, depth: int, home: str) -> tuple[str, str]:
    rel = path[len(home):] if path.startswith(home) else path
    parts = rel.strip("/").split("/")
    key = "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1]) or "~"
    display = f"~/{key}" if key != "~" else "~"
    prefix = str(Path(home) / key) if key != "~" and path.startswith(home) else key
    return display, prefix


def _recommendation(bucket: dict) -> tuple[str, str]:
    total = bucket["total"]
    skipped = bucket["statuses"].get("skip", 0) + bucket["statuses"].get("low", 0)
    valuable = bucket["statuses"].get("high", 0) + bucket["statuses"].get("medium", 0)
    untriaged = bucket["statuses"].get("untriaged", 0) + bucket["statuses"].get("unknown", 0)
    config_ratio = bucket["config"] / max(total, 1)

    if total >= 50 and valuable == 0 and (skipped / max(total, 1) > 0.6 or config_ratio > 0.65):
        return "exclude", "mostly skipped/config/generated-looking files"
    if valuable >= max(2, total * 0.2):
        return "include", "contains high/medium user-relevant files"
    if untriaged > 0:
        return "review", "needs triage or user confirmation"
    return "review", "mixed signal"


async def file_clusters(request: web.Request) -> web.Response:
    depth = max(1, min(int(request.query.get("depth", "3")), 6))
    limit = max(1, min(int(request.query.get("limit", "80")), 200))
    db = get_db()
    try:
        rows = db.execute(
            """SELECT path, file_type, size_bytes,
                      CASE WHEN triage_status = '' OR triage_status IS NULL THEN 'untriaged'
                           ELSE triage_status END as status
               FROM file_index"""
        ).fetchall()
    finally:
        db.close()

    home = str(Path.home())
    buckets: dict[str, dict] = {}
    for path, file_type, size, status in rows:
        display, prefix = _cluster_key(path, depth, home)
        bucket = buckets.setdefault(display, {
            "directory": display,
            "prefix": prefix,
            "total": 0,
            "total_size": 0,
            "statuses": {},
            "config": 0,
            "data": 0,
            "generated": 0,
        })
        ext = (file_type or "").lower()
        name = Path(path).name.lower()
        bucket["total"] += 1
        bucket["total_size"] += size or 0
        bucket["statuses"][status] = bucket["statuses"].get(status, 0) + 1
        if ext in _CONFIG_EXTS:
            bucket["config"] += 1
        if ext in _DATA_EXTS:
            bucket["data"] += 1
        if "generated" in name or ".gen." in name or name.endswith(".pb.go"):
            bucket["generated"] += 1

    clusters = []
    for bucket in buckets.values():
        action, reason = _recommendation(bucket)
        bucket["recommendation"] = action
        bucket["reason"] = reason
        clusters.append(bucket)

    clusters.sort(
        key=lambda b: (
            {"exclude": 0, "review": 1, "include": 2}.get(b["recommendation"], 3),
            -b["total"],
        )
    )
    return web.json_response({"depth": depth, "clusters": clusters[:limit], "total_clusters": len(clusters)})


async def file_cluster_decision(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    prefix = str(body.get("prefix") or "").strip()
    status = str(body.get("status") or "").strip().lower()
    if not prefix or status not in {"high", "medium", "low", "skip"}:
        return web.json_response({"error": "prefix and valid status are required"}, status=400)

    home = str(Path.home())
    if prefix.startswith("~/"):
        prefix = str(Path(home) / prefix[2:])
    if prefix == "~":
        return web.json_response({"error": "Refusing to update the entire home directory"}, status=400)

    db = get_db()
    try:
        cursor = db.execute(
            "UPDATE file_index SET triage_status = ? WHERE path = ? OR path LIKE ?",
            (status, prefix, f"{prefix}/%"),
        )
        db.commit()
        changed = cursor.rowcount or 0
    finally:
        db.close()

    return web.json_response({"success": True, "status": status, "updated": changed})
