"""Shared read model for Kith's personal insights surfaces.

The desktop app and web dashboard both use this module so the product-facing
"what should I look at today?" view stays consistent across frontends.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any


_CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml", ".xml", ".lock"}
_DATA_EXTS = {".csv", ".tsv", ".jsonl", ".sqlite", ".db"}
_DOC_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".md", ".txt", ".rst"}
_VIDEO_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "bilibili.com",
    "vimeo.com",
    "netflix.com",
    "iqiyi.com",
    "youku.com",
    "tiktok.com",
    "douyin.com",
    "twitch.tv",
    "coursera.org",
    "udemy.com",
}
_GENERATED_MARKERS = {
    "/node_modules/",
    "/.cache/",
    "/.cursor/extensions/",
    "/.cursor/projects/",
    "/.vscode/extensions/",
    "/.cursor-server/",
    "/site-packages/",
    "/miniconda3/pkgs/",
    "/miniconda3/envs/",
    "/miniconda3/lib/",
    "/miniconda3/share/",
    "/anaconda3/pkgs/",
    "/anaconda3/envs/",
    "/.conda/pkgs/",
    "/.conda/envs/",
    "/dist/",
    "/build/",
    "/target/",
    "/__pycache__/",
    "/.venv/",
    "/venv/",
    "/.next/",
    "/.turbo/",
}
_GENERATED_NAMES = {
    ".ds_store",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
}


def build_insights(db: sqlite3.Connection, *, limit: int = 12, now: float | None = None) -> dict[str, Any]:
    """Build a privacy-scoped insights payload from the local memory DB."""
    now = now or time.time()
    limit = max(4, min(int(limit or 12), 40))
    home = str(Path.home())

    overview = _build_overview(db, now)
    file_organization = _build_file_organization(db, home, limit=limit)
    cleanup_candidates = _build_cleanup_candidates(db, home, now, limit=limit)
    source_records = _load_source_records(db, limit=300)
    insight_items = _load_insight_items(db, limit=120)
    profile_facts = _load_profile_facts(db, limit=120)

    video_interests = _build_video_interests(source_records, insight_items, limit=limit)
    web_interests = _build_web_interests(source_records, insight_items, profile_facts, limit=limit)
    suggestions = _build_suggestions(
        overview,
        file_organization,
        cleanup_candidates,
        video_interests,
        web_interests,
        profile_facts,
    )

    return {
        "generated_at": now,
        "overview": overview,
        "file_organization": file_organization,
        "cleanup_candidates": cleanup_candidates,
        "video_interests": video_interests,
        "web_interests": web_interests,
        "suggestions": suggestions,
    }


def _build_overview(db: sqlite3.Connection, now: float) -> dict[str, Any]:
    total_files = _scalar(db, "SELECT COUNT(*) FROM file_index")
    summarized_files = _scalar(
        db,
        "SELECT COUNT(*) FROM file_index WHERE semantic_summary != '' AND semantic_summary IS NOT NULL",
    )
    knowledge_entries = _scalar(db, "SELECT COUNT(*) FROM knowledge")
    source_records = _scalar(db, "SELECT COUNT(*) FROM source_records")
    insight_items = _scalar(db, "SELECT COUNT(*) FROM insight_items WHERE status != 'hidden'")
    total_size = _scalar(db, "SELECT COALESCE(SUM(size_bytes), 0) FROM file_index")
    recent_7d = _scalar(db, "SELECT COUNT(*) FROM file_index WHERE modified_at > ?", (now - 7 * 86400,))
    latest_indexed = _scalar(db, "SELECT COALESCE(MAX(indexed_at), 0) FROM file_index")
    inferred_facts = _scalar(db, "SELECT COUNT(*) FROM profile_facts WHERE status = 'inferred'")
    confirmed_facts = _scalar(db, "SELECT COUNT(*) FROM profile_facts WHERE status = 'confirmed'")
    rag_pending = _rag_pending_count(db)

    confidence = 0.25
    if total_files:
        confidence += 0.25
    if summarized_files:
        confidence += 0.2
    if source_records or insight_items:
        confidence += 0.2
    if confirmed_facts:
        confidence += 0.1

    return {
        "total_files": total_files,
        "summarized_files": summarized_files,
        "knowledge_entries": knowledge_entries,
        "source_records": source_records,
        "insight_items": insight_items,
        "total_size_bytes": total_size,
        "recent_7d_modified": recent_7d,
        "latest_indexed_at": latest_indexed,
        "inferred_facts": inferred_facts,
        "confirmed_facts": confirmed_facts,
        "rag_pending": rag_pending,
        "confidence": round(min(confidence, 0.95), 2),
    }


def _build_file_organization(db: sqlite3.Connection, home: str, *, limit: int) -> list[dict[str, Any]]:
    rows = db.execute(
        """SELECT path, file_type, size_bytes, modified_at,
                  CASE WHEN triage_status = '' OR triage_status IS NULL THEN 'untriaged'
                       ELSE triage_status END as status
           FROM file_index"""
    ).fetchall()

    buckets: dict[str, dict[str, Any]] = {}
    for path, file_type, size, modified_at, status in rows:
        display, prefix = _cluster_key(str(path), 3, home)
        bucket = buckets.setdefault(
            display,
            {
                "directory": display,
                "prefix": prefix,
                "total": 0,
                "total_size": 0,
                "statuses": {},
                "config": 0,
                "data": 0,
                "documents": 0,
                "generated": 0,
                "last_modified": 0,
            },
        )
        ext = str(file_type or "").lower()
        name = Path(str(path)).name.lower()
        bucket["total"] += 1
        bucket["total_size"] += int(size or 0)
        bucket["statuses"][status] = bucket["statuses"].get(status, 0) + 1
        bucket["last_modified"] = max(float(modified_at or 0), bucket["last_modified"])
        if ext in _CONFIG_EXTS:
            bucket["config"] += 1
        if ext in _DATA_EXTS:
            bucket["data"] += 1
        if ext in _DOC_EXTS:
            bucket["documents"] += 1
        if _looks_generated(str(path), name):
            bucket["generated"] += 1

    clusters = []
    for bucket in buckets.values():
        action, reason, score = _cluster_recommendation(bucket)
        bucket["recommendation"] = action
        bucket["reason"] = reason
        bucket["score"] = score
        clusters.append(bucket)

    clusters.sort(
        key=lambda item: (
            {"exclude": 0, "review": 1, "include": 2}.get(item["recommendation"], 3),
            -item["score"],
            -item["total"],
        )
    )
    return clusters[:limit]


def _build_cleanup_candidates(
    db: sqlite3.Connection,
    home: str,
    now: float,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    cutoff = now - 90 * 86400
    rows = db.execute(
        """SELECT path, file_type, size_bytes, modified_at,
                  CASE WHEN triage_status = '' OR triage_status IS NULL THEN 'untriaged'
                       ELSE triage_status END as status,
                  priority,
                  COALESCE(semantic_summary, summary, '') as summary
           FROM file_index
           WHERE modified_at < ?
              OR triage_status IN ('skip', 'low')
              OR lower(path) LIKE '%/node_modules/%'
              OR lower(path) LIKE '%/.cache/%'
              OR lower(path) LIKE '%/dist/%'
              OR lower(path) LIKE '%/build/%'
           ORDER BY size_bytes DESC, modified_at ASC
           LIMIT 500""",
        (cutoff,),
    ).fetchall()

    candidates: list[dict[str, Any]] = []
    checked_missing = 0
    for path, file_type, size, modified_at, status, priority, summary in rows:
        raw_path = str(path)
        name = Path(raw_path).name.lower()
        size_bytes = int(size or 0)
        age_days = int(max(0, (now - float(modified_at or 0)) / 86400))
        reasons: list[str] = []
        risk = "medium"
        action = "Review before deleting"
        missing = False

        if status in {"skip", "low"}:
            reasons.append(f"marked {status} by triage")
        if age_days >= 180:
            reasons.append(f"not modified for {age_days} days")
        elif age_days >= 90:
            reasons.append(f"quiet for {age_days} days")
        if size_bytes >= 100 * 1024 * 1024:
            reasons.append("large file")
        if _looks_generated(raw_path, name):
            reasons.append("generated/cache-looking path")
            risk = "low"
            action = "Consider excluding from Kith"

        if checked_missing < 120:
            checked_missing += 1
            try:
                missing = not Path(raw_path).exists()
            except OSError:
                missing = False
            if missing:
                reasons.insert(0, "file is missing on disk")
                risk = "low"
                action = "Remove stale index entry"

        if status in {"high", "medium"} and not missing and not _looks_generated(raw_path, name):
            risk = "high"
            reasons.append("Kith previously found this potentially useful")

        if not reasons:
            continue

        candidates.append(
            {
                "path": _display_path(raw_path, home),
                "full_path": raw_path,
                "file_type": file_type or "unknown",
                "size_bytes": size_bytes,
                "modified_at": float(modified_at or 0),
                "age_days": age_days,
                "triage_status": status,
                "priority": priority,
                "summary": str(summary or "")[:220],
                "risk": risk,
                "reason": "; ".join(reasons[:4]),
                "action": action,
                "missing_on_disk": missing,
            }
        )
        if len(candidates) >= limit:
            break
    return candidates


def _build_video_interests(
    source_records: list[dict[str, Any]],
    insight_items: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for record in source_records:
        domain = str(record.get("domain") or "").lower()
        if not _is_video_domain(domain):
            continue
        bucket = buckets.setdefault(
            domain,
            {
                "domain": domain,
                "title": record.get("title") or domain,
                "count": 0,
                "last_seen": 0,
                "topics": [],
                "source_type": record.get("source_type", "browser_history"),
            },
        )
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        bucket["count"] += int(metadata.get("count") or 1)
        bucket["last_seen"] = max(float(record.get("occurred_at") or record.get("created_at") or 0), bucket["last_seen"])
        title = str(record.get("title") or "").strip()
        if title and title != domain and title not in bucket["topics"]:
            bucket["topics"].append(title[:80])

    browser_topics = [
        _strip_topic_prefix(str(item.get("statement") or ""))
        for item in insight_items
        if item.get("item_type") == "topic" and item.get("source_type") == "browser_history"
    ]
    for bucket in buckets.values():
        for topic in browser_topics[:4]:
            if topic and topic not in bucket["topics"]:
                bucket["topics"].append(topic)

    result = sorted(buckets.values(), key=lambda item: (item["count"], item["last_seen"]), reverse=True)
    return result[:limit]


def _build_web_interests(
    source_records: list[dict[str, Any]],
    insight_items: list[dict[str, Any]],
    profile_facts: list[dict[str, Any]],
    *,
    limit: int,
) -> dict[str, Any]:
    domain_counts: Counter[str] = Counter()
    domain_last_seen: dict[str, float] = {}
    bookmarks: list[dict[str, Any]] = []
    downloads: list[dict[str, Any]] = []

    for record in source_records:
        domain = str(record.get("domain") or "").lower()
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        count = int(metadata.get("count") or 1)
        if domain:
            domain_counts[domain] += count
            domain_last_seen[domain] = max(
                domain_last_seen.get(domain, 0),
                float(record.get("occurred_at") or record.get("created_at") or 0),
            )
        if record.get("source_type") == "browser_bookmark":
            bookmarks.append(record)
        elif record.get("source_type") == "browser_download":
            downloads.append(record)

    topics = []
    seen_topics: set[str] = set()
    for item in insight_items:
        if item.get("item_type") != "topic":
            continue
        topic = _strip_topic_prefix(str(item.get("statement") or ""))
        if topic and topic not in seen_topics:
            seen_topics.add(topic)
            topics.append(
                {
                    "topic": topic,
                    "confidence": item.get("confidence", 0.5),
                    "source_type": item.get("source_type", ""),
                    "updated_at": item.get("updated_at", 0),
                }
            )
    for fact in profile_facts:
        if fact.get("category") != "interest.browser":
            continue
        topic = str(fact.get("statement") or "").replace("你最近可能在关注", "").strip()
        if topic and topic not in seen_topics:
            seen_topics.add(topic)
            topics.append(
                {
                    "topic": topic,
                    "confidence": fact.get("confidence", 0.5),
                    "source_type": fact.get("source_type", ""),
                    "updated_at": fact.get("updated_at", 0),
                }
            )

    top_domains = [
        {
            "domain": domain,
            "count": count,
            "last_seen": domain_last_seen.get(domain, 0),
            "kind": "video" if _is_video_domain(domain) else "web",
        }
        for domain, count in domain_counts.most_common(limit)
    ]
    topics.sort(key=lambda item: (float(item.get("confidence") or 0), float(item.get("updated_at") or 0)), reverse=True)

    return {
        "top_domains": top_domains,
        "topics": topics[:limit],
        "bookmarks": [_source_preview(item) for item in bookmarks[:limit]],
        "downloads": [_source_preview(item) for item in downloads[:limit]],
        "has_browser_signal": bool(source_records or topics),
    }


def _build_suggestions(
    overview: dict[str, Any],
    file_organization: list[dict[str, Any]],
    cleanup_candidates: list[dict[str, Any]],
    video_interests: list[dict[str, Any]],
    web_interests: dict[str, Any],
    profile_facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []

    def add(kind: str, title: str, detail: str, action: str, priority: str = "medium") -> None:
        suggestions.append(
            {
                "kind": kind,
                "title": title,
                "detail": detail,
                "action": action,
                "priority": priority,
            }
        )

    inferred = [fact for fact in profile_facts if fact.get("status") == "inferred"]
    if inferred:
        add(
            "memory",
            "Confirm Kith's inferred memories",
            f"{len(inferred)} inferred facts can be corrected to improve future answers.",
            "Review memories",
            "medium",
        )

    if not web_interests.get("has_browser_signal"):
        add(
            "privacy",
            "Browser interests are not available",
            "Kith only shows web/video interests after you explicitly allow browser-history aggregation.",
            "Configure sources",
            "low",
        )
    elif video_interests:
        add(
            "video",
            f"Recent video signal: {video_interests[0]['domain']}",
            "Use this as a lightweight clue, not a private-content transcript.",
            "Ask Kith about this",
            "low",
        )

    review_cluster = next(
        (
            item
            for item in file_organization
            if item.get("recommendation") in {"exclude", "review"} and not _is_low_signal_cluster(item)
        ),
        None,
    )
    if review_cluster:
        add(
            "files",
            f"Review {review_cluster['directory']}",
            f"{review_cluster['total']} files, {review_cluster['reason']}.",
            "Open Triage",
            "high" if review_cluster.get("recommendation") == "exclude" else "medium",
        )

    if cleanup_candidates:
        add(
            "cleanup",
            f"{len(cleanup_candidates)} cleanup candidates",
            "These are old, large, skipped, generated-looking, or missing from disk.",
            "Review candidates",
            "medium",
        )
    if int(overview.get("rag_pending") or 0) > 0:
        add(
            "rag",
            "RAG index has pending files",
            f"{overview['rag_pending']} high/medium files do not have fresh chunks yet.",
            "Run RAG indexer",
            "medium",
        )

    return suggestions[:8]


def _load_source_records(db: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    try:
        rows = db.execute(
            """SELECT id, source_type, source_ref, title, domain, path,
                      occurred_at, created_at, metadata
               FROM source_records
               ORDER BY COALESCE(occurred_at, created_at) DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "id": r[0],
            "source_type": r[1],
            "source_ref": r[2],
            "title": r[3] or "",
            "domain": r[4] or "",
            "path": r[5] or "",
            "occurred_at": r[6] or 0,
            "created_at": r[7] or 0,
            "metadata": _safe_json(r[8]),
        }
        for r in rows
    ]


def _load_insight_items(db: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    try:
        rows = db.execute(
            """SELECT id, run_id, item_type, statement, source_type, source_ref,
                      confidence, status, created_at, updated_at, metadata
               FROM insight_items
               WHERE status != 'hidden'
               ORDER BY updated_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "id": r[0],
            "run_id": r[1],
            "item_type": r[2],
            "statement": r[3],
            "source_type": r[4] or "",
            "source_ref": r[5] or "",
            "confidence": r[6],
            "status": r[7],
            "created_at": r[8],
            "updated_at": r[9],
            "metadata": _safe_json(r[10]),
        }
        for r in rows
    ]


def _load_profile_facts(db: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    try:
        rows = db.execute(
            """SELECT id, category, statement, source_type, source_ref,
                      confidence, status, created_at, updated_at, metadata
               FROM profile_facts
               WHERE status != 'hidden'
               ORDER BY updated_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "id": r[0],
            "category": r[1],
            "statement": r[2],
            "source_type": r[3],
            "source_ref": r[4],
            "confidence": r[5],
            "status": r[6],
            "created_at": r[7],
            "updated_at": r[8],
            "metadata": _safe_json(r[9]),
        }
        for r in rows
    ]


def _rag_pending_count(db: sqlite3.Connection) -> int:
    try:
        return int(
            db.execute(
                """SELECT COUNT(*)
                   FROM file_index fi
                   WHERE fi.triage_status IN ('high', 'medium')
                     AND NOT EXISTS (
                       SELECT 1 FROM document_chunks dc
                       WHERE dc.path = fi.path AND dc.file_hash = fi.hash
                     )"""
            ).fetchone()[0]
            or 0
        )
    except sqlite3.OperationalError:
        return 0


def _cluster_key(path: str, depth: int, home: str) -> tuple[str, str]:
    rel = path[len(home):] if path.startswith(home) else path
    parts = rel.strip("/").split("/")
    key = "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1]) or "~"
    display = f"~/{key}" if key != "~" else "~"
    prefix = str(Path(home) / key) if key != "~" and path.startswith(home) else key
    return display, prefix


def _cluster_recommendation(bucket: dict[str, Any]) -> tuple[str, str, float]:
    total = int(bucket["total"])
    statuses = bucket["statuses"]
    skipped = int(statuses.get("skip", 0)) + int(statuses.get("low", 0))
    valuable = int(statuses.get("high", 0)) + int(statuses.get("medium", 0))
    untriaged = int(statuses.get("untriaged", 0)) + int(statuses.get("unknown", 0))
    generated_ratio = (int(bucket["generated"]) + int(bucket["config"])) / max(total, 1)
    valuable_ratio = valuable / max(total, 1)
    skip_ratio = skipped / max(total, 1)

    if _is_noise_parent_cluster(bucket):
        return "exclude", "known tool/cache/dependency directory", 1.0
    if total >= 20 and valuable == 0 and (skip_ratio > 0.55 or generated_ratio > 0.5):
        return "exclude", "mostly skipped, config, or generated-looking files", round(skip_ratio + generated_ratio, 2)
    if valuable >= max(2, total * 0.18):
        return "include", "contains high/medium user-relevant files", round(valuable_ratio, 2)
    if untriaged > 0:
        return "review", "needs triage or user confirmation", round(untriaged / max(total, 1), 2)
    return "review", "mixed signal", round(max(skip_ratio, valuable_ratio, generated_ratio), 2)


def _looks_generated(path: str, name: str) -> bool:
    lowered = path.lower()
    return (
        name in _GENERATED_NAMES
        or name.endswith((".log", ".tmp", ".cache", ".pyc", ".map"))
        or any(marker in lowered for marker in _GENERATED_MARKERS)
        or "generated" in name
        or ".gen." in name
    )


def _is_noise_parent_cluster(item: dict[str, Any]) -> bool:
    path = f"{item.get('prefix', '')} {item.get('directory', '')}".lower()
    return any(marker in path for marker in _GENERATED_MARKERS)


def _is_low_signal_cluster(item: dict[str, Any]) -> bool:
    """Keep obvious tooling/cache clusters out of top-level product suggestions."""
    path = f"{item.get('prefix', '')} {item.get('directory', '')}".lower()
    if any(marker in path for marker in _GENERATED_MARKERS):
        return True

    total = max(int(item.get("total") or 0), 1)
    generated = int(item.get("generated") or 0)
    config = int(item.get("config") or 0)
    low_signal_ratio = (generated + config) / total
    return item.get("recommendation") == "exclude" and low_signal_ratio > 0.65


def _is_video_domain(domain: str) -> bool:
    return any(domain == item or domain.endswith(f".{item}") for item in _VIDEO_DOMAINS)


def _source_preview(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": record.get("title") or record.get("domain") or Path(str(record.get("path") or "")).name,
        "domain": record.get("domain") or "",
        "path": record.get("path") or "",
        "occurred_at": record.get("occurred_at") or record.get("created_at") or 0,
        "metadata": record.get("metadata") if isinstance(record.get("metadata"), dict) else {},
    }


def _strip_topic_prefix(statement: str) -> str:
    return statement.replace("最近关注主题：", "").replace("你最近可能在关注", "").strip()


def _display_path(path: str, home: str) -> str:
    return "~" + path[len(home):] if path.startswith(home) else path


def _safe_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _scalar(db: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    try:
        row = db.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0) if row else 0
