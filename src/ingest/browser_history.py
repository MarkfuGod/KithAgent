"""Privacy-preserving browser history ingestion for first-run onboarding.

Reads Chromium-family History SQLite DBs directly, but never touches cookies,
session stores, passwords, or query strings. The output is a small aggregate
used to make the first profile feel useful within minutes.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sqlite3
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


CHROME_EPOCH_OFFSET_SECONDS = 11644473600

_STOPWORDS = {
    "the", "and", "for", "with", "from", "you", "your", "about", "watch",
    "video", "search", "google", "login", "home", "page", "http", "https",
    "www", "com", "html", "utm", "index", "news",
    "书签栏", "其他书签", "书签", "收藏夹", "收藏", "阅读列表", "未命名文件夹",
}


@dataclass(frozen=True)
class BrowserHistoryEntry:
    title: str
    url: str
    domain: str
    visit_count: int
    last_visit_time: float


@dataclass(frozen=True)
class BrowserBookmarkEntry:
    title: str
    url: str
    domain: str
    folder: str


@dataclass(frozen=True)
class BrowserDownloadEntry:
    target_path: str
    url: str
    domain: str
    start_time: float


def chrome_time_to_unix(value: int | float | None) -> float:
    if not value:
        return 0.0
    return max(0.0, (float(value) / 1_000_000) - CHROME_EPOCH_OFFSET_SECONDS)


def sanitize_url(raw: str) -> tuple[str, str] | None:
    """Return (safe_url_without_query, domain) or None for browser/internal URLs."""
    try:
        parsed = urlsplit(raw)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    safe = urlunsplit((parsed.scheme, parsed.netloc, parsed.path[:160], "", ""))
    return safe, domain


def default_profile_dirs() -> list[Path]:
    home = Path.home()
    roots = [
        home / "Library/Application Support/Google/Chrome",
        home / "Library/Application Support/Microsoft Edge",
        home / "Library/Application Support/BraveSoftware/Brave-Browser",
        home / "Library/Application Support/Arc/User Data",
    ]
    profiles: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for child in root.iterdir():
            if child.name in {"Default", "Profile 1", "Profile 2", "Profile 3"} or child.name.startswith("Profile"):
                profiles.append(child)
    return profiles


def default_history_paths() -> list[Path]:
    return [profile / "History" for profile in default_profile_dirs() if (profile / "History").exists()]


def default_bookmark_paths() -> list[Path]:
    return [profile / "Bookmarks" for profile in default_profile_dirs() if (profile / "Bookmarks").exists()]


class BrowserHistoryIngestor:
    """Collect sanitized, aggregate signals from Chromium History DBs."""

    def __init__(
        self,
        history_paths: list[Path] | None = None,
        bookmark_paths: list[Path] | None = None,
    ):
        self.history_paths = history_paths if history_paths is not None else default_history_paths()
        self.bookmark_paths = (
            bookmark_paths
            if bookmark_paths is not None
            else (default_bookmark_paths() if history_paths is None else [])
        )

    async def collect(self, *, days: int = 30, limit: int = 500) -> dict:
        return await asyncio.to_thread(self._collect_sync, days, limit)

    def _collect_sync(self, days: int, limit: int) -> dict:
        cutoff = time.time() - max(days, 1) * 86400
        entries: list[BrowserHistoryEntry] = []
        bookmarks: list[BrowserBookmarkEntry] = []
        downloads: list[BrowserDownloadEntry] = []
        sources: list[str] = []
        per_source_limit = max(50, limit // max(len(self.history_paths), 1))

        for history_path in self.history_paths:
            if not history_path.exists():
                continue
            try:
                rows = self._read_history(history_path, cutoff, per_source_limit)
            except Exception:
                continue
            if rows:
                sources.append(str(history_path))
                entries.extend(rows)
            try:
                download_rows = self._read_downloads(history_path, cutoff, per_source_limit)
            except Exception:
                download_rows = []
            if download_rows:
                downloads.extend(download_rows)

        for bookmark_path in self.bookmark_paths:
            if not bookmark_path.exists():
                continue
            try:
                rows = self._read_bookmarks(bookmark_path)
            except Exception:
                continue
            if rows:
                sources.append(str(bookmark_path))
                bookmarks.extend(rows)

        entries.sort(key=lambda e: e.last_visit_time, reverse=True)
        entries = entries[:limit]
        downloads.sort(key=lambda e: e.start_time, reverse=True)
        downloads = downloads[: min(limit, 100)]
        domain_counts: Counter[str] = Counter()
        domain_counts.update(e.domain for e in entries)
        domain_counts.update(e.domain for e in bookmarks)
        domain_counts.update(e.domain for e in downloads if e.domain)
        topics = self._extract_topics(entries, bookmarks, downloads)

        return {
            "enabled": True,
            "sources": sources,
            "entries_count": len(entries),
            "bookmarks_count": len(bookmarks),
            "downloads_count": len(downloads),
            "top_domains": [
                {"domain": domain, "count": count}
                for domain, count in domain_counts.most_common(20)
            ],
            "topics": [{"topic": topic, "count": count} for topic, count in topics],
            "sample_titles": [e.title for e in entries if e.title][:40],
            "bookmarks": [
                {
                    "title": b.title,
                    "domain": b.domain,
                    "folder": b.folder,
                    "url": b.url,
                }
                for b in bookmarks[:40]
            ],
            "downloads": [
                {
                    "target_path": d.target_path,
                    "domain": d.domain,
                    "url": d.url,
                    "start_time": d.start_time,
                }
                for d in downloads[:40]
            ],
        }

    def _read_history(self, path: Path, cutoff: float, limit: int) -> list[BrowserHistoryEntry]:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=True) as tmp:
            shutil.copy2(path, tmp.name)
            db = sqlite3.connect(tmp.name)
            try:
                rows = db.execute(
                    """SELECT title, url, visit_count, last_visit_time
                       FROM urls
                       WHERE last_visit_time > ?
                       ORDER BY last_visit_time DESC
                       LIMIT ?""",
                    (
                        int((cutoff + CHROME_EPOCH_OFFSET_SECONDS) * 1_000_000),
                        limit,
                    ),
                ).fetchall()
            finally:
                db.close()

        entries: list[BrowserHistoryEntry] = []
        for title, raw_url, visit_count, last_visit_time in rows:
            sanitized = sanitize_url(raw_url or "")
            if not sanitized:
                continue
            safe_url, domain = sanitized
            entries.append(
                BrowserHistoryEntry(
                    title=(title or "").strip()[:180],
                    url=safe_url,
                    domain=domain,
                    visit_count=int(visit_count or 0),
                    last_visit_time=chrome_time_to_unix(last_visit_time),
                )
            )
        return entries

    def _read_bookmarks(self, path: Path) -> list[BrowserBookmarkEntry]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        roots = raw.get("roots", {}) if isinstance(raw, dict) else {}
        entries: list[BrowserBookmarkEntry] = []

        def walk(node: dict, folders: list[str]) -> None:
            node_type = node.get("type")
            name = str(node.get("name") or "").strip()
            if node_type == "folder":
                next_folders = folders + ([name] if name else [])
                for child in node.get("children", []) or []:
                    if isinstance(child, dict):
                        walk(child, next_folders)
                return

            if node_type != "url":
                return
            sanitized = sanitize_url(str(node.get("url") or ""))
            if not sanitized:
                return
            safe_url, domain = sanitized
            entries.append(
                BrowserBookmarkEntry(
                    title=name[:180],
                    url=safe_url,
                    domain=domain,
                    folder="/".join(folders[-3:]),
                )
            )

        for root in roots.values():
            if isinstance(root, dict):
                walk(root, [])
        return entries

    def _read_downloads(self, path: Path, cutoff: float, limit: int) -> list[BrowserDownloadEntry]:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=True) as tmp:
            shutil.copy2(path, tmp.name)
            db = sqlite3.connect(tmp.name)
            try:
                rows = db.execute(
                    """SELECT
                           COALESCE(NULLIF(d.target_path, ''), d.current_path, '') AS target_path,
                           COALESCE(duc.url, d.tab_url, d.referrer, '') AS url,
                           d.start_time
                       FROM downloads d
                       LEFT JOIN downloads_url_chains duc
                         ON d.id = duc.id AND COALESCE(duc.chain_index, 0) = 0
                       WHERE d.start_time > ?
                       ORDER BY d.start_time DESC
                       LIMIT ?""",
                    (
                        int((cutoff + CHROME_EPOCH_OFFSET_SECONDS) * 1_000_000),
                        limit,
                    ),
                ).fetchall()
            finally:
                db.close()

        entries: list[BrowserDownloadEntry] = []
        for target_path, raw_url, start_time in rows:
            sanitized = sanitize_url(raw_url or "")
            if sanitized:
                safe_url, domain = sanitized
            else:
                safe_url, domain = "", ""
            target = str(target_path or "").strip()
            if not target and not safe_url:
                continue
            entries.append(
                BrowserDownloadEntry(
                    target_path=target,
                    url=safe_url,
                    domain=domain,
                    start_time=chrome_time_to_unix(start_time),
                )
            )
        return entries

    def _extract_topics(
        self,
        entries: list[BrowserHistoryEntry],
        bookmarks: list[BrowserBookmarkEntry],
        downloads: list[BrowserDownloadEntry],
    ) -> list[tuple[str, int]]:
        counts: Counter[str] = Counter()
        for entry in entries:
            text = f"{entry.title} {entry.domain}".lower()
            for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text):
                token = token.strip("-_")
                if token and token not in _STOPWORDS:
                    counts[token] += 1
        for bookmark in bookmarks:
            text = f"{bookmark.title} {bookmark.folder} {bookmark.domain}".lower()
            for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text):
                token = token.strip("-_")
                if token and token not in _STOPWORDS:
                    counts[token] += 2
        for download in downloads:
            text = f"{Path(download.target_path).stem} {download.domain}".lower()
            for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text):
                token = token.strip("-_")
                if token and token not in _STOPWORDS:
                    counts[token] += 1
        return counts.most_common(30)
