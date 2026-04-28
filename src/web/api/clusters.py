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
_NOISE_PARENT_MARKERS = (
    "/.cursor/extensions/",
    "/.cursor/projects/",
    "/.vscode/extensions/",
    "/.cursor-server/",
    "/node_modules/",
    "/site-packages/",
    "/miniconda3/pkgs/",
    "/miniconda3/envs/",
    "/miniconda3/lib/",
    "/miniconda3/share/",
    "/anaconda3/pkgs/",
    "/anaconda3/envs/",
    "/.conda/pkgs/",
    "/.conda/envs/",
    "/.gradle/caches/",
    "/.m2/repository/",
    "/go/pkg/",
    "/.npm/",
    "/.cargo/",
    "/.rustup/",
)


def _cluster_key(path: str, depth: int, home: str) -> tuple[str, str]:
    rel = path[len(home):] if path.startswith(home) else path
    parts = rel.strip("/").split("/")
    key = "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1]) or "~"
    display = f"~/{key}" if key != "~" else "~"
    prefix = str(Path(home) / key) if key != "~" and path.startswith(home) else key
    return display, prefix


def _display_prefix(prefix: str, home: str) -> str:
    if prefix.startswith(home):
        rel = prefix[len(home):].strip("/")
        return f"~/{rel}" if rel else "~"
    return prefix


def _normalize_prefix(prefix: str, home: str) -> str:
    prefix = prefix.strip()
    if prefix.startswith("~/"):
        return str(Path(home) / prefix[2:])
    return prefix


def _noise_parent_key(path: str, home: str) -> tuple[str, str] | None:
    """Collapse known tool/cache/dependency subtrees to their stable parent."""
    lowered = path.lower()
    for raw_marker in _NOISE_PARENT_MARKERS:
        marker = raw_marker.strip("/").lower()
        needle = f"/{marker}/"
        idx = lowered.find(needle)
        if idx < 0 and lowered.endswith(f"/{marker}"):
            idx = len(lowered) - len(f"/{marker}")
        if idx < 0:
            continue
        prefix_end = idx + len(marker) + 1
        prefix = path[:prefix_end]
        return _display_prefix(prefix, home), prefix
    return None


def _recommendation(bucket: dict) -> tuple[str, str]:
    total = bucket["total"]
    skipped = bucket["statuses"].get("skip", 0) + bucket["statuses"].get("low", 0)
    valuable = bucket["statuses"].get("high", 0) + bucket["statuses"].get("medium", 0)
    untriaged = bucket["statuses"].get("untriaged", 0) + bucket["statuses"].get("unknown", 0)
    config_ratio = bucket["config"] / max(total, 1)

    if bucket.get("noise_parent") or _matches_noise_parent(bucket):
        return "exclude", "known tool/cache/dependency parent; safe to skip"
    if total >= 50 and valuable == 0 and (skipped / max(total, 1) > 0.6 or config_ratio > 0.65):
        return "exclude", "mostly skipped/config/generated-looking files"
    if valuable >= max(2, total * 0.2):
        return "include", "contains high/medium user-relevant files"
    if untriaged > 0:
        return "review", "needs triage or user confirmation"
    return "review", "mixed signal"


def _matches_noise_parent(bucket: dict) -> bool:
    path = f"{bucket.get('prefix', '')} {bucket.get('directory', '')}".lower()
    return any(marker in path for marker in _NOISE_PARENT_MARKERS)


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
        noise_key = _noise_parent_key(path, home)
        display, prefix = noise_key or _cluster_key(path, depth, home)
        bucket = buckets.setdefault(display, {
            "directory": display,
            "prefix": prefix,
            "total": 0,
            "total_size": 0,
            "statuses": {},
            "config": 0,
            "data": 0,
            "generated": 0,
            "noise_parent": False,
            "samples": [],
        })
        if noise_key:
            bucket["noise_parent"] = True
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
        if len(bucket["samples"]) < 8:
            bucket["samples"].append({
                "path": _display_prefix(path, home),
                "file_type": ext or "(no suffix)",
                "size_bytes": size or 0,
                "status": status,
            })

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


async def file_cluster_files(request: web.Request) -> web.Response:
    prefix = str(request.query.get("prefix") or "").strip()
    if not prefix:
        return web.json_response({"error": "prefix is required"}, status=400)

    limit = max(1, min(int(request.query.get("limit", "200")), 300))
    home = str(Path.home())
    normalized_prefix = _normalize_prefix(prefix, home)

    db = get_db()
    try:
        total = db.execute(
            "SELECT COUNT(*) FROM file_index WHERE path = ? OR path LIKE ?",
            (normalized_prefix, f"{normalized_prefix}/%"),
        ).fetchone()[0]
        rows = db.execute(
            """SELECT path, file_type, size_bytes,
                      CASE WHEN triage_status = '' OR triage_status IS NULL THEN 'untriaged'
                           ELSE triage_status END as status
               FROM file_index
               WHERE path = ? OR path LIKE ?
               ORDER BY
                 CASE status
                   WHEN 'untriaged' THEN 0
                   WHEN 'unknown' THEN 1
                   WHEN 'skip' THEN 2
                   WHEN 'low' THEN 3
                   WHEN 'medium' THEN 4
                   WHEN 'high' THEN 5
                   ELSE 6
                 END,
                 size_bytes DESC,
                 path ASC
               LIMIT ?""",
            (normalized_prefix, f"{normalized_prefix}/%", limit),
        ).fetchall()
    finally:
        db.close()

    files = [
        {
            "path": _display_prefix(path, home),
            "file_type": (file_type or "").lower() or "(no suffix)",
            "size_bytes": size or 0,
            "status": status,
        }
        for path, file_type, size, status in rows
    ]
    return web.json_response({
        "prefix": _display_prefix(normalized_prefix, home),
        "total_files": total,
        "files": files,
        "limit": limit,
    })


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
