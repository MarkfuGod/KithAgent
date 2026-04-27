"""
Memory Store — the 'RAM + Disk' of AgentOS.

Two layers:
  1. Hot cache   (in-memory dict, LRU eviction) — like CPU L1/L2 cache
  2. Cold store  (SQLite + optional vector search) — like disk/swap

Every file indexed by the FileSystem watcher is stored here.
External agents query Memory through the Syscall API instead of
re-reading and re-parsing files every time.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time
from collections import OrderedDict
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from src.kernel.config import MemoryConfig

logger = logging.getLogger("agent_sys.memory")


class LRUCache:
    """Simple LRU cache — analogous to CPU cache hierarchy."""

    def __init__(self, max_items: int = 10000):
        self._max = max_items
        self._data: OrderedDict[str, Any] = OrderedDict()

    def get(self, key: str) -> Any | None:
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def put(self, key: str, value: Any) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def invalidate(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


class MemoryStore:
    """Persistent knowledge store with hot/cold layers."""

    def __init__(self, config: MemoryConfig):
        self.config = config
        self._cache = LRUCache(config.cache_max_items)
        self._db: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        db_path = Path(str(self.config.db_path)).expanduser().resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=30000")
        self._create_tables()
        logger.info("Memory store initialized at %s", db_path)

    def _create_tables(self) -> None:
        assert self._db
        # Step 1: create tables (v0.1 compatible — no new columns here)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS file_index (
                path        TEXT PRIMARY KEY,
                hash        TEXT NOT NULL,
                size_bytes  INTEGER,
                modified_at REAL,
                indexed_at  REAL NOT NULL,
                file_type   TEXT,
                summary     TEXT,
                metadata    TEXT
            );

            CREATE TABLE IF NOT EXISTS knowledge (
                id          TEXT PRIMARY KEY,
                category    TEXT NOT NULL,
                content     TEXT NOT NULL,
                source_path TEXT,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL,
                metadata    TEXT
            );

            CREATE TABLE IF NOT EXISTS agent_context (
                session_id  TEXT PRIMARY KEY,
                agent_name  TEXT NOT NULL,
                context     TEXT NOT NULL,
                created_at  REAL NOT NULL,
                expires_at  REAL
            );

            CREATE TABLE IF NOT EXISTS profile_facts (
                id          TEXT PRIMARY KEY,
                category    TEXT NOT NULL,
                statement   TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_ref  TEXT,
                confidence  REAL NOT NULL,
                status      TEXT NOT NULL,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL,
                metadata    TEXT
            );

            CREATE TABLE IF NOT EXISTS source_records (
                id          TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_ref  TEXT NOT NULL,
                title       TEXT,
                domain      TEXT,
                path        TEXT,
                occurred_at REAL,
                created_at  REAL NOT NULL,
                metadata    TEXT
            );

            CREATE TABLE IF NOT EXISTS insight_runs (
                id            TEXT PRIMARY KEY,
                run_type      TEXT NOT NULL,
                status        TEXT NOT NULL,
                started_at    REAL NOT NULL,
                completed_at  REAL,
                elapsed_s     REAL,
                input_counts  TEXT,
                output_summary TEXT,
                metadata      TEXT
            );

            CREATE TABLE IF NOT EXISTS insight_items (
                id          TEXT PRIMARY KEY,
                run_id      TEXT NOT NULL,
                item_type   TEXT NOT NULL,
                statement   TEXT NOT NULL,
                source_type TEXT,
                source_ref  TEXT,
                confidence  REAL NOT NULL,
                status      TEXT NOT NULL,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL,
                metadata    TEXT
            );

            CREATE TABLE IF NOT EXISTS document_chunks (
                id              TEXT PRIMARY KEY,
                path            TEXT NOT NULL,
                file_hash       TEXT NOT NULL,
                chunk_index     INTEGER NOT NULL,
                start_line      INTEGER,
                end_line        INTEGER,
                content         TEXT NOT NULL,
                created_at      REAL NOT NULL,
                embedding       BLOB,
                embedding_model TEXT DEFAULT '',
                metadata        TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_file_type ON file_index(file_type);
            CREATE INDEX IF NOT EXISTS idx_knowledge_cat ON knowledge(category);
            CREATE INDEX IF NOT EXISTS idx_agent_ctx_name ON agent_context(agent_name);
            CREATE INDEX IF NOT EXISTS idx_profile_facts_status ON profile_facts(status);
            CREATE INDEX IF NOT EXISTS idx_profile_facts_category ON profile_facts(category);
            CREATE INDEX IF NOT EXISTS idx_source_records_type ON source_records(source_type);
            CREATE INDEX IF NOT EXISTS idx_source_records_domain ON source_records(domain);
            CREATE INDEX IF NOT EXISTS idx_insight_runs_type ON insight_runs(run_type, started_at);
            CREATE INDEX IF NOT EXISTS idx_insight_items_run ON insight_items(run_id);
            CREATE INDEX IF NOT EXISTS idx_insight_items_status ON insight_items(status);
            CREATE INDEX IF NOT EXISTS idx_document_chunks_path ON document_chunks(path);
            CREATE INDEX IF NOT EXISTS idx_document_chunks_hash ON document_chunks(path, file_hash);
        """)

        try:
            self._db.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts
                   USING fts5(id UNINDEXED, path UNINDEXED, content)"""
            )
        except sqlite3.OperationalError as e:
            logger.warning("SQLite FTS5 unavailable; RAG exact retrieval disabled: %s", e)

        # Step 2: migrate — add v0.2 columns to existing tables
        self._migrate_schema()

        # Step 3: indexes that depend on migrated columns
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_file_priority ON file_index(priority)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_file_triage ON file_index(triage_status)")
        self._db.commit()

    def _migrate_schema(self) -> None:
        """Add new columns to existing tables if they don't exist yet."""
        assert self._db
        existing = {
            row[1] for row in self._db.execute("PRAGMA table_info(file_index)").fetchall()
        }
        migrations = [
            ("priority", "INTEGER DEFAULT 1"),
            ("semantic_summary", "TEXT DEFAULT ''"),
            ("last_accessed_at", "REAL DEFAULT 0"),
            ("triage_status", "TEXT DEFAULT ''"),
            ("embedding", "BLOB"),
            ("embedding_model", "TEXT DEFAULT ''"),
        ]
        for col, typedef in migrations:
            if col not in existing:
                self._db.execute(f"ALTER TABLE file_index ADD COLUMN {col} {typedef}")
                logger.info("Migrated file_index: added column %s", col)
        self._db.commit()

    # ── File index operations ─────────────────────────────────

    async def upsert_file(
        self,
        path: str,
        content_hash: str,
        size_bytes: int,
        modified_at: float,
        file_type: str,
        summary: str = "",
        metadata: dict | None = None,
    ) -> None:
        now = time.time()
        record = {
            "path": path,
            "hash": content_hash,
            "size_bytes": size_bytes,
            "modified_at": modified_at,
            "indexed_at": now,
            "file_type": file_type,
            "summary": summary,
            "metadata": json.dumps(metadata or {}),
        }

        async with self._lock:
            db = self._db
            assert db

            def _run() -> None:
                db.execute(
                    """INSERT OR REPLACE INTO file_index
                       (path, hash, size_bytes, modified_at, indexed_at, file_type, summary, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        path, content_hash, size_bytes, modified_at, now,
                        file_type, summary, record["metadata"],
                    ),
                )
                db.commit()

            await asyncio.to_thread(_run)
            self._cache.put(f"file:{path}", record)

    async def get_file_info(self, path: str) -> dict | None:
        cached = self._cache.get(f"file:{path}")
        if cached:
            return cached
        db = self._db
        assert db

        def _run():
            return db.execute(
                """SELECT path, hash, size_bytes, modified_at, indexed_at,
                          file_type, summary, metadata
                   FROM file_index WHERE path = ?""",
                (path,),
            ).fetchone()

        async with self._lock:
            row = await asyncio.to_thread(_run)
        if row:
            info = dict(zip(
                ["path", "hash", "size_bytes", "modified_at",
                 "indexed_at", "file_type", "summary", "metadata"],
                row,
            ))
            self._cache.put(f"file:{path}", info)
            return info
        return None

    async def search_files(self, query: str, file_type: str | None = None, limit: int = 20) -> list[dict]:
        """Search indexed files — uses vector search for natural language queries,
        falls back to SQL LIKE for short keyword queries."""
        assert self._db

        use_vector = len(query.split()) >= 3
        if use_vector:
            try:
                from src.memory.embeddings import embed_text, is_available
                if is_available():
                    q_emb = embed_text(query)
                    if q_emb:
                        results = await self.vector_search(q_emb, limit=limit)
                        if file_type:
                            results = [r for r in results if r.get("file_type") == file_type]
                        if results:
                            return results
            except Exception as e:
                # Vector search is best-effort — fall through to SQL LIKE
                # but log so operators can diagnose silent search degradation.
                logger.warning(
                    "Vector search failed, falling back to SQL LIKE (query=%r): %s",
                    query[:60], e,
                )

        sql = """SELECT path, file_type, summary, size_bytes, priority,
                        COALESCE(semantic_summary, '') as semantic_summary
                 FROM file_index
                 WHERE (path LIKE ? OR summary LIKE ? OR semantic_summary LIKE ?)"""
        params: list[Any] = [f"%{query}%", f"%{query}%", f"%{query}%"]
        if file_type:
            sql += " AND file_type = ?"
            params.append(file_type)
        sql += " ORDER BY priority ASC, indexed_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(sql, params).fetchall()
        return [
            {"path": r[0], "file_type": r[1], "summary": r[2],
             "size_bytes": r[3], "priority": r[4], "semantic_summary": r[5]}
            for r in rows
        ]

    async def get_files_needing_summary(
        self,
        limit: int = 50,
        *,
        allowed_triage_statuses: list[str] | tuple[str, ...] | None = None,
        include_untriaged: bool = False,
    ) -> list[dict]:
        """Return files that need summarization, prioritized by triage importance.

        Order:
          1. triage_status='high' (most important)
          2. triage_status='medium'
          3. optional untriaged fallback only when explicitly requested
        Files marked 'skip', 'low', or 'unknown' are excluded entirely. The
        default is intentionally strict: summarize only files that triage
        already marked high/medium.
        Within each tier, we diversify across code / doc / image categories
        so a single huge code tree doesn't starve documents and images.
        """
        assert self._db

        _code_ext = ("'.py'","'.js'","'.ts'","'.go'","'.rs'","'.sh'","'.java'","'.c'","'.cpp'","'.swift'",
                      "'.json'","'.yaml'","'.yml'","'.toml'","'.xml'","'.csv'")
        _doc_ext = ("'.pdf'","'.docx'","'.doc'","'.pptx'","'.xlsx'","'.md'","'.txt'","'.rst'","'.tex'")
        _img_ext = ("'.png'","'.jpg'","'.jpeg'","'.gif'","'.webp'","'.bmp'")

        code_limit = max(limit // 2, 1)
        doc_limit = max(limit // 4, 1)
        img_limit = max(limit // 4, 1)

        allowed = tuple(allowed_triage_statuses or ("high", "medium"))
        valid_allowed = [s for s in allowed if s in {"high", "medium"}]
        if not valid_allowed:
            valid_allowed = ["high", "medium"]
        placeholders = ",".join("?" for _ in valid_allowed)
        triage_filter = f"AND triage_status IN ({placeholders})"
        triage_params: list[Any] = list(valid_allowed)
        if include_untriaged:
            triage_filter = (
                f"AND (triage_status IN ({placeholders}) "
                "OR triage_status = '' OR triage_status IS NULL)"
            )
        triage_order = """CASE triage_status
            WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2
        END"""

        results = []
        for ext_group, grp_limit in [
            (_code_ext, code_limit),
            (_doc_ext, doc_limit),
            (_img_ext, img_limit),
        ]:
            ext_list = ",".join(ext_group)
            rows = self._db.execute(
                f"""SELECT path, hash, file_type, size_bytes, summary, modified_at,
                           triage_status
                    FROM file_index
                    WHERE (semantic_summary = '' OR semantic_summary IS NULL)
                      AND file_type IN ({ext_list})
                      {triage_filter}
                    ORDER BY {triage_order}, priority ASC, modified_at DESC
                    LIMIT ?""",
                (*triage_params, grp_limit),
            ).fetchall()
            results.extend(rows)

        # Preserve the triage-aware order from SQL (was previously clobbered
        # by a Python-side `sort(key=size_bytes, reverse=True)` that made
        # large-untriaged files cut in front of small high-priority ones).
        # We do want a stable tier ordering across ext groups, so re-sort
        # by the SQL triage_rank only.
        _rank = {"high": 0, "medium": 1}
        results.sort(key=lambda r: (_rank.get(r[6], 2), -(r[5] or 0)))

        return [
            {"path": r[0], "hash": r[1], "file_type": r[2], "size_bytes": r[3],
             "summary": r[4], "modified_at": r[5], "triage_status": r[6]}
            for r in results[:limit]
        ]

    async def update_semantic_summary(self, path: str, semantic_summary: str) -> None:
        async with self._lock:
            assert self._db
            self._db.execute(
                "UPDATE file_index SET semantic_summary = ? WHERE path = ?",
                (semantic_summary, path),
            )
            self._db.commit()

    async def update_embedding(self, path: str, embedding: bytes, model_name: str = "") -> None:
        async with self._lock:
            assert self._db
            self._db.execute(
                "UPDATE file_index SET embedding = ?, embedding_model = ? WHERE path = ?",
                (embedding, model_name, path),
            )
            self._db.commit()

    async def batch_update_embeddings(self, updates: list[tuple[str, bytes, str]]) -> None:
        """Bulk update embeddings: [(path, embedding_bytes, model_name), ...]"""
        if not updates:
            return
        db = self._db
        assert db
        payload = [(emb, model, path) for path, emb, model in updates]

        def _run() -> None:
            db.executemany(
                "UPDATE file_index SET embedding = ?, embedding_model = ? WHERE path = ?",
                payload,
            )
            db.commit()

        async with self._lock:
            await asyncio.to_thread(_run)

    async def get_files_needing_embedding(self, limit: int = 200) -> list[dict]:
        """Return files that have summaries but no embeddings yet (high/medium triage only)."""
        assert self._db
        rows = self._db.execute(
            """SELECT path, semantic_summary
               FROM file_index
               WHERE semantic_summary != '' AND semantic_summary IS NOT NULL
                 AND (embedding IS NULL OR embedding = '')
                 AND triage_status IN ('high', 'medium')
               ORDER BY
                 CASE triage_status WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                 modified_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [{"path": r[0], "semantic_summary": r[1]} for r in rows]

    async def vector_search(self, query_embedding: bytes, limit: int = 20) -> list[dict]:
        """Find files by cosine similarity to the query embedding.

        Runs the DB read + numerical compute in a worker thread so we
        don't block the event loop. Uses numpy for a single batched
        dot product when available, falling back to a per-row loop
        using embeddings.cosine_similarity otherwise.
        """
        assert self._db
        db = self._db

        def _do_search() -> list[dict]:
            rows = db.execute(
                """SELECT path, file_type, semantic_summary, size_bytes, priority, embedding
                   FROM file_index
                   WHERE embedding IS NOT NULL AND length(embedding) > 0"""
            ).fetchall()
            if not rows:
                return []

            try:
                import numpy as np
                q = np.frombuffer(query_embedding, dtype=np.float32)
                if q.size == 0 or not np.isfinite(q).all():
                    return []
                q_norm = float(np.linalg.norm(q)) + 1e-9

                paths, ftypes, summaries, sizes, prios, scores = [], [], [], [], [], []
                # Process in chunks so a single giant matrix doesn't blow memory
                chunk_size = 4096
                for start in range(0, len(rows), chunk_size):
                    chunk = rows[start:start + chunk_size]
                    vectors = []
                    vector_rows = []
                    for r in chunk:
                        v = np.frombuffer(r[5], dtype=np.float32)
                        if v.shape != q.shape or v.size == 0 or not np.isfinite(v).all():
                            continue
                        vectors.append(v)
                        vector_rows.append(r)
                    if not vectors:
                        continue
                    try:
                        mat = np.stack(vectors)
                    except ValueError:
                        # Mismatched dimensions across rows — fall back to per-row
                        from src.memory.embeddings import cosine_similarity as _cs
                        for r in vector_rows:
                            try:
                                s = _cs(query_embedding, r[5])
                            except Exception:
                                continue
                            paths.append(r[0]); ftypes.append(r[1])
                            summaries.append(r[2]); sizes.append(r[3])
                            prios.append(r[4]); scores.append(float(s))
                        continue
                    norms = np.linalg.norm(mat, axis=1) + 1e-9
                    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
                        sims = (mat @ q) / (norms * q_norm)
                    for r, s in zip(vector_rows, sims):
                        if not np.isfinite(s):
                            continue
                        paths.append(r[0]); ftypes.append(r[1])
                        summaries.append(r[2]); sizes.append(r[3])
                        prios.append(r[4]); scores.append(float(s))

                idx_sorted = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:limit]
                return [
                    {
                        "path": paths[i], "file_type": ftypes[i],
                        "semantic_summary": summaries[i], "size_bytes": sizes[i],
                        "priority": prios[i], "score": round(scores[i], 4),
                    }
                    for i in idx_sorted
                ]
            except ImportError:
                # numpy not installed — fall back to per-row Python loop.
                from src.memory.embeddings import cosine_similarity
                scored = []
                for r in rows:
                    try:
                        score = cosine_similarity(query_embedding, r[5])
                        scored.append((score, r))
                    except Exception:
                        continue
                scored.sort(key=lambda x: x[0], reverse=True)
                return [
                    {
                        "path": r[0], "file_type": r[1], "semantic_summary": r[2],
                        "size_bytes": r[3], "priority": r[4], "score": round(score, 4),
                    }
                    for score, r in scored[:limit]
                ]

        return await asyncio.to_thread(_do_search)

    async def update_file_priority(self, path: str, priority: int) -> None:
        async with self._lock:
            assert self._db
            self._db.execute(
                "UPDATE file_index SET priority = ? WHERE path = ?",
                (priority, path),
            )
            self._db.commit()

    async def batch_update_priorities(self, updates: list[tuple[str, int]]) -> None:
        """Bulk update priorities: [(path, priority), ...]"""
        if not updates:
            return
        db = self._db
        assert db
        payload = [(p, path) for path, p in updates]

        def _run() -> None:
            db.executemany(
                "UPDATE file_index SET priority = ? WHERE path = ?",
                payload,
            )
            db.commit()

        async with self._lock:
            await asyncio.to_thread(_run)

    async def get_file_modification_stats(self) -> list[dict]:
        """Aggregate file modification data for behavior analysis."""
        assert self._db
        rows = self._db.execute(
            """SELECT
                 file_type,
                 COUNT(*) as file_count,
                 AVG(size_bytes) as avg_size,
                 MAX(modified_at) as latest_modified,
                 MIN(modified_at) as earliest_modified
               FROM file_index
               GROUP BY file_type
               ORDER BY file_count DESC"""
        ).fetchall()
        return [
            {
                "file_type": r[0], "file_count": r[1], "avg_size": r[2],
                "latest_modified": r[3], "earliest_modified": r[4],
            }
            for r in rows
        ]

    async def get_recently_modified_files(self, hours: float = 24, limit: int = 100) -> list[dict]:
        """Files modified in the last N hours, for behavior analysis."""
        assert self._db
        cutoff = time.time() - (hours * 3600)
        rows = self._db.execute(
            """SELECT path, file_type, modified_at, size_bytes, priority
               FROM file_index WHERE modified_at > ?
               ORDER BY modified_at DESC LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
        return [
            {"path": r[0], "file_type": r[1], "modified_at": r[2], "size_bytes": r[3], "priority": r[4]}
            for r in rows
        ]

    async def get_directory_activity(self, depth: int = 3) -> list[dict]:
        """Aggregate activity by directory for behavior analysis."""
        assert self._db
        rows = self._db.execute(
            """SELECT path, modified_at, file_type FROM file_index
               ORDER BY modified_at DESC LIMIT 5000"""
        ).fetchall()

        from collections import Counter
        dir_counts: Counter[str] = Counter()
        for path, mtime, ftype in rows:
            parts = path.split("/")
            dir_path = "/".join(parts[:min(depth + 1, len(parts) - 1)])
            if dir_path:
                dir_counts[dir_path] += 1

        return [
            {"directory": d, "file_count": c}
            for d, c in dir_counts.most_common(30)
        ]

    async def get_all_file_paths_with_priority(self) -> list[tuple[str, float, int]]:
        """Return (path, modified_at, priority) for the priority classifier."""
        assert self._db
        rows = self._db.execute(
            "SELECT path, modified_at, priority FROM file_index"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    async def get_modification_rate(self, minutes: float = 30) -> int:
        """Count files modified in the last N minutes (for activity detection)."""
        assert self._db
        cutoff = time.time() - (minutes * 60)
        row = self._db.execute(
            "SELECT COUNT(*) FROM file_index WHERE modified_at > ?", (cutoff,)
        ).fetchone()
        return row[0] if row else 0

    async def get_project_directories(self, min_files: int = 3) -> list[dict]:
        """Identify project root directories by looking for marker files."""
        assert self._db
        markers = ("pyproject.toml", "package.json", "Cargo.toml", "go.mod",
                    "requirements.txt", "Makefile", "setup.py", ".git")
        project_dirs: list[dict] = []
        for marker in markers:
            rows = self._db.execute(
                "SELECT path FROM file_index WHERE path LIKE ?",
                (f"%/{marker}",),
            ).fetchall()
            for (p,) in rows:
                d = str(Path(p).parent)
                project_dirs.append({"directory": d, "marker": marker})

        # Deduplicate by directory, count files per project
        seen: dict[str, dict] = {}
        for pd in project_dirs:
            d = pd["directory"]
            if d not in seen:
                count = self._db.execute(
                    "SELECT COUNT(*) FROM file_index WHERE path LIKE ?",
                    (f"{d}/%",),
                ).fetchone()[0]
                if count >= min_files:
                    seen[d] = {"directory": d, "file_count": count, "marker": pd["marker"]}
        return sorted(seen.values(), key=lambda x: x["file_count"], reverse=True)

    async def get_directory_breakdown(self, depth: int = 2) -> list[dict]:
        """Group ALL files by top-level directory and file category for holistic analysis."""
        assert self._db
        rows = self._db.execute(
            "SELECT path, file_type, size_bytes FROM file_index"
        ).fetchall()

        from collections import defaultdict
        dir_stats: dict[str, dict] = defaultdict(lambda: {
            "code": 0, "document": 0, "image": 0, "data": 0, "other": 0,
            "total": 0, "total_size": 0,
        })

        _code_ext = {".py", ".js", ".ts", ".go", ".rs", ".sh", ".java", ".c", ".cpp", ".swift", ".kt", ".rb", ".php"}
        _doc_ext = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".md", ".txt", ".rst", ".tex"}
        _img_ext = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
        _data_ext = {".json", ".yaml", ".yml", ".toml", ".xml", ".csv"}

        home = str(Path.home())
        for path, ftype, size in rows:
            rel = path[len(home):] if path.startswith(home) else path
            parts = rel.strip("/").split("/")
            dir_key = "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1]) or "root"

            s = dir_stats[dir_key]
            s["total"] += 1
            s["total_size"] += (size or 0)

            ext = (ftype or "").lower()
            if ext in _code_ext:
                s["code"] += 1
            elif ext in _doc_ext:
                s["document"] += 1
            elif ext in _img_ext:
                s["image"] += 1
            elif ext in _data_ext:
                s["data"] += 1
            else:
                s["other"] += 1

        result = []
        for d, s in sorted(dir_stats.items(), key=lambda x: x[1]["total"], reverse=True):
            s["directory"] = d
            result.append(s)
        return result[:50]

    async def get_files_by_category(self, category: str, limit: int = 50) -> list[dict]:
        """Get files by category (document, image, etc.) for non-code analysis."""
        assert self._db
        ext_map = {
            "document": ("'.pdf'", "'.docx'", "'.doc'", "'.pptx'", "'.xlsx'", "'.txt'", "'.md'"),
            "image": ("'.png'", "'.jpg'", "'.jpeg'", "'.gif'", "'.webp'", "'.bmp'"),
        }
        exts = ext_map.get(category)
        if not exts:
            return []

        ext_list = ",".join(exts)
        rows = self._db.execute(
            f"""SELECT path, file_type, size_bytes, modified_at,
                       COALESCE(semantic_summary, '') as semantic_summary
                FROM file_index
                WHERE file_type IN ({ext_list})
                ORDER BY modified_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {"path": r[0], "file_type": r[1], "size_bytes": r[2],
             "modified_at": r[3], "semantic_summary": r[4]}
            for r in rows
        ]

    async def get_files_by_directory(self, directory: str) -> list[dict]:
        """Get all indexed files under a directory with their summaries."""
        assert self._db
        rows = self._db.execute(
            """SELECT path, file_type, semantic_summary, size_bytes
               FROM file_index WHERE path LIKE ?
               ORDER BY priority ASC, modified_at DESC""",
            (f"{directory}/%",),
        ).fetchall()
        return [
            {"path": r[0], "file_type": r[1], "semantic_summary": r[2] or "", "size_bytes": r[3]}
            for r in rows
        ]

    async def get_recent_scheduling_decisions(self, limit: int = 5) -> list[dict]:
        """Retrieve recent adaptive scheduling decisions for LLM context."""
        return await self.query_knowledge(category="scheduling_decision", limit=limit)

    # ── Chunk RAG operations ───────────────────────────────────

    @staticmethod
    def _fts_query(text: str) -> str:
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
        tokens = [t for t in tokens if len(t) > 1][:12]
        return " OR ".join(tokens)

    async def delete_document_chunks_for_path(self, path: str) -> int:
        """Remove all RAG chunks for a file path."""
        db = self._db
        assert db

        def _run() -> int:
            ids = [r[0] for r in db.execute(
                "SELECT id FROM document_chunks WHERE path = ?",
                (path,),
            ).fetchall()]
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            try:
                db.execute(f"DELETE FROM document_chunks_fts WHERE id IN ({placeholders})", ids)
            except sqlite3.OperationalError:
                pass
            db.execute(f"DELETE FROM document_chunks WHERE id IN ({placeholders})", ids)
            db.commit()
            return len(ids)

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def upsert_document_chunks(
        self,
        path: str,
        file_hash: str,
        chunks: list[dict[str, Any]],
    ) -> int:
        """Replace a file's chunks and FTS rows in one transaction."""
        db = self._db
        assert db
        now = time.time()

        def _run() -> int:
            old_ids = [r[0] for r in db.execute(
                "SELECT id FROM document_chunks WHERE path = ?",
                (path,),
            ).fetchall()]
            if old_ids:
                placeholders = ",".join("?" for _ in old_ids)
                try:
                    db.execute(f"DELETE FROM document_chunks_fts WHERE id IN ({placeholders})", old_ids)
                except sqlite3.OperationalError:
                    pass
                db.execute(f"DELETE FROM document_chunks WHERE id IN ({placeholders})", old_ids)

            rows = []
            fts_rows = []
            for chunk in chunks:
                chunk_index = int(chunk.get("chunk_index", len(rows)))
                content = str(chunk.get("content") or "").strip()
                if not content:
                    continue
                chunk_id = chunk.get("id") or f"{file_hash}:{hashlib.sha1(f'{path}:{chunk_index}:{content[:80]}'.encode()).hexdigest()[:16]}"
                rows.append((
                    chunk_id,
                    path,
                    file_hash,
                    chunk_index,
                    chunk.get("start_line"),
                    chunk.get("end_line"),
                    content,
                    now,
                    json.dumps(chunk.get("metadata") or {}),
                ))
                fts_rows.append((chunk_id, path, content))

            if rows:
                db.executemany(
                    """INSERT INTO document_chunks
                       (id, path, file_hash, chunk_index, start_line, end_line,
                        content, created_at, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                try:
                    db.executemany(
                        "INSERT INTO document_chunks_fts (id, path, content) VALUES (?, ?, ?)",
                        fts_rows,
                    )
                except sqlite3.OperationalError as e:
                    logger.warning("Failed to update document chunk FTS rows: %s", e)
            db.commit()
            return len(rows)

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def get_files_needing_rag_index(
        self,
        limit: int = 20,
        *,
        allowed_triage_statuses: list[str] | tuple[str, ...] | None = None,
        max_file_size_bytes: int | None = None,
    ) -> list[dict]:
        """Files whose chunk index is missing or stale for the current hash."""
        db = self._db
        assert db
        statuses = [s for s in (allowed_triage_statuses or ("high", "medium")) if s in {"high", "medium"}]
        if not statuses:
            statuses = ["high", "medium"]
        placeholders = ",".join("?" for _ in statuses)
        size_clause = "AND size_bytes <= ?" if max_file_size_bytes else ""
        params: list[Any] = [*statuses]
        if max_file_size_bytes:
            params.append(max_file_size_bytes)
        params.append(limit)

        rows = db.execute(
            f"""SELECT path, hash, file_type, size_bytes, modified_at,
                       COALESCE(semantic_summary, '') as semantic_summary
                FROM file_index fi
                WHERE triage_status IN ({placeholders})
                  {size_clause}
                  AND NOT EXISTS (
                    SELECT 1 FROM document_chunks dc
                    WHERE dc.path = fi.path AND dc.file_hash = fi.hash
                  )
                ORDER BY
                  CASE triage_status WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                  modified_at DESC
                LIMIT ?""",
            params,
        ).fetchall()
        return [
            {
                "path": r[0], "hash": r[1], "file_type": r[2],
                "size_bytes": r[3], "modified_at": r[4], "semantic_summary": r[5],
            }
            for r in rows
        ]

    async def get_chunks_needing_embedding(
        self,
        limit: int = 100,
        *,
        embedding_model: str = "",
    ) -> list[dict]:
        db = self._db
        assert db
        if embedding_model:
            where = "(embedding IS NULL OR length(embedding) = 0 OR embedding_model != ?)"
            params: list[Any] = [embedding_model, limit]
        else:
            where = "(embedding IS NULL OR length(embedding) = 0)"
            params = [limit]
        rows = db.execute(
            f"""SELECT id, path, chunk_index, content
                FROM document_chunks
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ?""",
            params,
        ).fetchall()
        return [
            {"id": r[0], "path": r[1], "chunk_index": r[2], "content": r[3]}
            for r in rows
        ]

    async def batch_update_chunk_embeddings(self, updates: list[tuple[str, bytes, str]]) -> None:
        if not updates:
            return
        db = self._db
        assert db

        def _run() -> None:
            db.executemany(
                "UPDATE document_chunks SET embedding = ?, embedding_model = ? WHERE id = ?",
                [(emb, model, chunk_id) for chunk_id, emb, model in updates],
            )
            db.commit()

        async with self._lock:
            await asyncio.to_thread(_run)

    async def fts_search_chunks(
        self,
        query: str,
        limit: int = 20,
        *,
        allowed_triage_statuses: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict]:
        db = self._db
        assert db
        fts_query = self._fts_query(query)
        if not fts_query:
            return []
        statuses = [s for s in (allowed_triage_statuses or ("high", "medium")) if s in {"high", "medium"}]
        if not statuses:
            statuses = ["high", "medium"]
        placeholders = ",".join("?" for _ in statuses)
        params: list[Any] = [fts_query, *statuses, limit]

        def _run() -> list[dict]:
            try:
                rows = db.execute(
                    f"""SELECT dc.id, dc.path, dc.chunk_index, dc.start_line, dc.end_line,
                               dc.content, fi.file_type, bm25(document_chunks_fts) as rank
                        FROM document_chunks_fts
                        JOIN document_chunks dc ON dc.id = document_chunks_fts.id
                        JOIN file_index fi ON fi.path = dc.path
                        WHERE document_chunks_fts MATCH ?
                          AND fi.triage_status IN ({placeholders})
                        ORDER BY rank ASC
                        LIMIT ?""",
                    params,
                ).fetchall()
            except sqlite3.OperationalError:
                like = f"%{query}%"
                rows = db.execute(
                    f"""SELECT dc.id, dc.path, dc.chunk_index, dc.start_line, dc.end_line,
                               dc.content, fi.file_type, 0.0 as rank
                        FROM document_chunks dc
                        JOIN file_index fi ON fi.path = dc.path
                        WHERE dc.content LIKE ?
                          AND fi.triage_status IN ({placeholders})
                        ORDER BY dc.created_at DESC
                        LIMIT ?""",
                    [like, *statuses, limit],
                ).fetchall()
            return [
                {
                    "chunk_id": r[0], "path": r[1], "chunk_index": r[2],
                    "start_line": r[3], "end_line": r[4], "content": r[5],
                    "file_type": r[6], "fts_rank": float(r[7] or 0.0),
                    "retrieval_mode": "fts",
                }
                for r in rows
            ]

        return await asyncio.to_thread(_run)

    async def vector_search_chunks(
        self,
        query_embedding: bytes,
        limit: int = 20,
        *,
        allowed_triage_statuses: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict]:
        db = self._db
        assert db
        statuses = [s for s in (allowed_triage_statuses or ("high", "medium")) if s in {"high", "medium"}]
        if not statuses:
            statuses = ["high", "medium"]
        placeholders = ",".join("?" for _ in statuses)

        def _run() -> list[dict]:
            rows = db.execute(
                f"""SELECT dc.id, dc.path, dc.chunk_index, dc.start_line, dc.end_line,
                           dc.content, fi.file_type, dc.embedding
                    FROM document_chunks dc
                    JOIN file_index fi ON fi.path = dc.path
                    WHERE dc.embedding IS NOT NULL AND length(dc.embedding) > 0
                      AND fi.triage_status IN ({placeholders})""",
                statuses,
            ).fetchall()
            if not rows:
                return []
            try:
                import numpy as np
                q = np.frombuffer(query_embedding, dtype=np.float32)
                q_norm = float(np.linalg.norm(q)) + 1e-9
                scored: list[tuple[float, Any]] = []
                for r in rows:
                    v = np.frombuffer(r[7], dtype=np.float32)
                    if v.shape != q.shape or v.size == 0:
                        continue
                    score = float((v @ q) / ((np.linalg.norm(v) + 1e-9) * q_norm))
                    if np.isfinite(score):
                        scored.append((score, r))
            except ImportError:
                from src.memory.embeddings import cosine_similarity
                scored = []
                for r in rows:
                    try:
                        scored.append((cosine_similarity(query_embedding, r[7]), r))
                    except Exception:
                        continue
            scored.sort(key=lambda item: item[0], reverse=True)
            return [
                {
                    "chunk_id": r[0], "path": r[1], "chunk_index": r[2],
                    "start_line": r[3], "end_line": r[4], "content": r[5],
                    "file_type": r[6], "score": round(score, 4),
                    "retrieval_mode": "vector",
                }
                for score, r in scored[:limit]
            ]

        return await asyncio.to_thread(_run)

    async def hybrid_search_chunks(
        self,
        query: str,
        *,
        limit: int = 6,
        fts_limit: int = 20,
        vector_limit: int = 20,
        min_score: float = 0.05,
        allowed_triage_statuses: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict]:
        """Hybrid RAG retrieval over chunks, with best-effort vector fallback."""
        fts_results = await self.fts_search_chunks(
            query, limit=fts_limit, allowed_triage_statuses=allowed_triage_statuses,
        )
        vector_results: list[dict] = []
        try:
            from src.memory.embeddings import embed_text, is_available
            if is_available():
                q_emb = embed_text(query)
                if q_emb:
                    vector_results = await self.vector_search_chunks(
                        q_emb, limit=vector_limit, allowed_triage_statuses=allowed_triage_statuses,
                    )
        except Exception as e:
            logger.debug("Chunk vector retrieval unavailable: %s", e)

        merged: dict[str, dict] = {}
        for rank, item in enumerate(fts_results):
            score = 1.0 / (rank + 1)
            current = merged.setdefault(item["chunk_id"], {**item, "hybrid_score": 0.0, "modes": []})
            current["hybrid_score"] += score
            current["modes"].append("fts")
        for rank, item in enumerate(vector_results):
            score = max(float(item.get("score", 0.0)), 0.0) + (0.25 / (rank + 1))
            current = merged.setdefault(item["chunk_id"], {**item, "hybrid_score": 0.0, "modes": []})
            current["hybrid_score"] += score
            current["score"] = max(float(current.get("score", 0.0)), float(item.get("score", 0.0)))
            current["modes"].append("vector")

        results = [
            item for item in merged.values()
            if float(item.get("hybrid_score", 0.0)) >= min_score
        ]
        results.sort(key=lambda item: item.get("hybrid_score", 0.0), reverse=True)
        for idx, item in enumerate(results[:limit], 1):
            item["source_id"] = f"S{idx}"
            item["hybrid_score"] = round(float(item.get("hybrid_score", 0.0)), 4)
            item["content"] = str(item.get("content", ""))[:1200]
            item["modes"] = sorted(set(item.get("modes", [])))
        return results[:limit]

    async def count_document_chunks(self) -> int:
        assert self._db
        return self._db.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]

    # ── Triage operations ──────────────────────────────────────

    async def get_untriaged_files(
        self,
        limit: int = 500,
        type_priority: dict[str, int] | None = None,
    ) -> list[dict]:
        """Return untriaged files ordered by (type_priority, recency).

        `type_priority` maps file extension → 1..10 hint. Higher hint means the
        file type is considered more likely to reveal the user as a person,
        so it's analyzed first when token budget is limited. Unknown types
        default to 5. Falls back to pure modified_at DESC ordering when no
        priority map is given.
        """
        assert self._db

        if type_priority:
            _valid_ext = {
                ext for ext in type_priority
                if isinstance(ext, str) and ext.startswith(".")
                and all(c.isalnum() or c in "._-" for c in ext[1:])
                and 1 <= len(ext) <= 12
            }
            cases = []
            for ext in _valid_ext:
                prio = int(type_priority[ext])
                prio = max(1, min(10, prio))
                cases.append(f"WHEN '{ext}' THEN {prio}")
            case_expr = (
                "CASE file_type " + " ".join(cases) + " ELSE 5 END"
                if cases else "5"
            )
            sql = f"""SELECT path, file_type, size_bytes, modified_at
                      FROM file_index
                      WHERE (triage_status = '' OR triage_status IS NULL)
                      ORDER BY {case_expr} DESC, modified_at DESC
                      LIMIT ?"""
        else:
            sql = """SELECT path, file_type, size_bytes, modified_at
                     FROM file_index
                     WHERE (triage_status = '' OR triage_status IS NULL)
                     ORDER BY modified_at DESC
                     LIMIT ?"""

        rows = self._db.execute(sql, (limit,)).fetchall()
        return [
            {"path": r[0], "file_type": r[1], "size_bytes": r[2], "modified_at": r[3]}
            for r in rows
        ]

    async def get_triage_stats(self) -> dict:
        """Return triage status distribution."""
        assert self._db
        rows = self._db.execute(
            """SELECT
                 CASE WHEN triage_status = '' OR triage_status IS NULL THEN 'untriaged'
                      ELSE triage_status END as status,
                 COUNT(*) as cnt
               FROM file_index
               GROUP BY status
               ORDER BY cnt DESC"""
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    async def batch_update_triage(self, updates: list[tuple[str, str]]) -> None:
        """Bulk update triage status: [(path, status), ...]"""
        if not updates:
            return
        db = self._db
        assert db
        payload = [(status, path) for path, status in updates]

        def _run() -> None:
            db.executemany(
                "UPDATE file_index SET triage_status = ? WHERE path = ?",
                payload,
            )
            db.commit()

        async with self._lock:
            await asyncio.to_thread(_run)

    async def batch_update_triage_by_prefix(self, prefix: str, status: str) -> int:
        """Mark all files under a directory prefix with the given triage status.
        Returns the number of rows affected."""
        db = self._db
        assert db

        def _run() -> int:
            cursor = db.execute(
                "UPDATE file_index SET triage_status = ? WHERE path LIKE ? "
                "AND (triage_status = '' OR triage_status IS NULL)",
                (status, f"{prefix}%"),
            )
            db.commit()
            return cursor.rowcount or 0

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def batch_update_triage_by_extensions(self, extensions: list[str], status: str) -> int:
        """Mark untriaged files with matching extensions."""
        normalized = sorted({
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in extensions
            if ext
        })
        if not normalized:
            return 0
        db = self._db
        assert db
        placeholders = ",".join("?" for _ in normalized)

        def _run() -> int:
            cursor = db.execute(
                f"UPDATE file_index SET triage_status = ? "
                f"WHERE lower(file_type) IN ({placeholders}) "
                "AND (triage_status = '' OR triage_status IS NULL)",
                (status, *normalized),
            )
            db.commit()
            return cursor.rowcount or 0

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def batch_update_triage_by_file_patterns(self, patterns: list[str], status: str) -> int:
        """Mark untriaged files whose basename or full path matches fnmatch patterns."""
        normalized = [p for p in patterns if p]
        if not normalized:
            return 0
        db = self._db
        assert db

        def _run() -> int:
            rows = db.execute(
                "SELECT path FROM file_index WHERE triage_status = '' OR triage_status IS NULL"
            ).fetchall()
            updates: list[tuple[str, str]] = []
            for (path,) in rows:
                name = Path(path).name
                rel = str(Path(path).expanduser())
                if any(fnmatch(name, pat) or fnmatch(rel, pat) for pat in normalized):
                    updates.append((status, path))
            if not updates:
                return 0
            db.executemany("UPDATE file_index SET triage_status = ? WHERE path = ?", updates)
            db.commit()
            return len(updates)

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def park_remaining_as_unknown(self) -> int:
        """Mark every still-untriaged row as 'unknown' so downstream
        agents don't keep re-queueing them when no LLM is available.
        Returns the number of rows changed."""
        db = self._db
        assert db

        def _run() -> int:
            cursor = db.execute(
                "UPDATE file_index SET triage_status = 'unknown' "
                "WHERE (triage_status = '' OR triage_status IS NULL)"
            )
            db.commit()
            return cursor.rowcount or 0

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def reopen_unknown(self) -> int:
        """Reset 'unknown'-parked rows back to untriaged so a fresh
        LLM-powered triage run reclassifies them."""
        db = self._db
        assert db

        def _run() -> int:
            cursor = db.execute(
                "UPDATE file_index SET triage_status = '' WHERE triage_status = 'unknown'"
            )
            db.commit()
            return cursor.rowcount or 0

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def prune_out_of_scope(self, watch_paths: list[str]) -> int:
        """Mark rows whose path is outside any of `watch_paths` as 'skip'.

        Meant for startup hygiene: if the user narrows their scan scope
        (e.g. from ~/ down to ~/Documents + ~/Desktop), file_index still
        contains 100k+ rows from the old scan — downstream agents would
        happily keep summarizing them. This flips them to 'skip' so the
        summarizer/triage queries skip them without needing a full wipe.

        Rows already marked 'skip' are left alone. Returns the count of
        rows changed.
        """
        expanded: list[str] = []
        for p in watch_paths or []:
            if not p:
                continue
            try:
                expanded.append(str(Path(p).expanduser().resolve()))
            except Exception:
                continue
        if not expanded:
            return 0

        db = self._db
        assert db

        def _run() -> int:
            where_out = " AND ".join(["path NOT LIKE ?"] * len(expanded))
            params = [f"{p}%" for p in expanded]
            cursor = db.execute(
                f"UPDATE file_index SET triage_status = 'skip' "
                f"WHERE (triage_status IS NULL OR triage_status != 'skip') "
                f"AND ({where_out})",
                params,
            )
            db.commit()
            return cursor.rowcount or 0

        async with self._lock:
            changed = await asyncio.to_thread(_run)
            if changed:
                self._cache.clear()
            return changed

    async def remove_file(self, path: str) -> None:
        async with self._lock:
            assert self._db
            ids = [r[0] for r in self._db.execute(
                "SELECT id FROM document_chunks WHERE path = ?",
                (path,),
            ).fetchall()]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                try:
                    self._db.execute(f"DELETE FROM document_chunks_fts WHERE id IN ({placeholders})", ids)
                except sqlite3.OperationalError:
                    pass
                self._db.execute(f"DELETE FROM document_chunks WHERE id IN ({placeholders})", ids)
            self._db.execute("DELETE FROM file_index WHERE path = ?", (path,))
            self._db.commit()
            self._cache.invalidate(f"file:{path}")

    # ── Knowledge operations ──────────────────────────────────

    async def store_knowledge(
        self,
        knowledge_id: str | None = None,
        category: str = "",
        content: str = "",
        source_path: str = "",
        metadata: dict | None = None,
        *,
        kid: str | None = None,  # deprecated alias, kept for internal back-compat
    ) -> None:
        """Persist a knowledge entry.

        `knowledge_id` is the primary key. The old parameter name `kid`
        is still accepted as a keyword for a deprecation window.
        """
        if knowledge_id is None:
            knowledge_id = kid
        if knowledge_id is None:
            raise TypeError("store_knowledge requires 'knowledge_id'")
        async with self._lock:
            now = time.time()
            assert self._db
            self._db.execute(
                """INSERT OR REPLACE INTO knowledge
                   (id, category, content, source_path, created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (knowledge_id, category, content, source_path, now, now, json.dumps(metadata or {})),
            )
            self._db.commit()

    async def query_knowledge(self, category: str | None = None, limit: int = 50) -> list[dict]:
        assert self._db
        if category:
            rows = self._db.execute(
                "SELECT id, category, content, source_path FROM knowledge WHERE category = ? ORDER BY updated_at DESC LIMIT ?",
                (category, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT id, category, content, source_path FROM knowledge ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"id": r[0], "category": r[1], "content": r[2], "source": r[3]} for r in rows]

    # ── Correctable profile facts ──────────────────────────────

    async def upsert_profile_fact(
        self,
        fact_id: str,
        category: str,
        statement: str,
        source_type: str = "inferred",
        source_ref: str = "",
        confidence: float = 0.5,
        status: str = "inferred",
        metadata: dict | None = None,
    ) -> None:
        """Store a user-profile fact while preserving explicit user feedback."""
        statement = statement.strip()
        if not fact_id or not statement:
            return

        async with self._lock:
            now = time.time()
            assert self._db
            existing = self._db.execute(
                "SELECT status, created_at FROM profile_facts WHERE id = ?",
                (fact_id,),
            ).fetchone()
            created_at = existing[1] if existing else now
            if existing and existing[0] in {"confirmed", "rejected", "hidden"}:
                status = existing[0]

            self._db.execute(
                """INSERT OR REPLACE INTO profile_facts
                   (id, category, statement, source_type, source_ref,
                    confidence, status, created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fact_id, category, statement, source_type, source_ref,
                    float(max(0, min(1, confidence))), status, created_at, now,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            self._db.commit()

    async def list_profile_facts(
        self,
        status: str | None = None,
        category: str | None = None,
        limit: int = 50,
        include_hidden: bool = False,
    ) -> list[dict]:
        assert self._db
        sql = """SELECT id, category, statement, source_type, source_ref,
                        confidence, status, created_at, updated_at, metadata
                 FROM profile_facts WHERE 1=1"""
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        elif not include_hidden:
            sql += " AND status != 'hidden'"
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY CASE status WHEN 'confirmed' THEN 0 WHEN 'inferred' THEN 1 ELSE 2 END, updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(sql, params).fetchall()
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
                "metadata": json.loads(r[9] or "{}"),
            }
            for r in rows
        ]

    async def update_profile_fact_status(self, fact_id: str, status: str) -> bool:
        if status not in {"inferred", "confirmed", "rejected", "hidden"}:
            raise ValueError(f"Invalid profile fact status: {status}")
        async with self._lock:
            assert self._db
            cursor = self._db.execute(
                "UPDATE profile_facts SET status = ?, updated_at = ? WHERE id = ?",
                (status, time.time(), fact_id),
            )
            self._db.commit()
            return bool(cursor.rowcount)

    # ── Source records + insight runs ──────────────────────────

    async def upsert_source_record(
        self,
        record_id: str,
        source_type: str,
        source_ref: str,
        title: str = "",
        domain: str = "",
        path: str = "",
        occurred_at: float = 0.0,
        metadata: dict | None = None,
    ) -> None:
        """Store a provenance record used to explain profile/insight claims."""
        if not record_id or not source_type or not source_ref:
            return
        async with self._lock:
            assert self._db
            self._db.execute(
                """INSERT OR REPLACE INTO source_records
                   (id, source_type, source_ref, title, domain, path,
                    occurred_at, created_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    source_type,
                    source_ref,
                    title,
                    domain,
                    path,
                    occurred_at,
                    time.time(),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            self._db.commit()

    async def list_source_records(
        self,
        source_type: str | None = None,
        domain: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        assert self._db
        sql = """SELECT id, source_type, source_ref, title, domain, path,
                        occurred_at, created_at, metadata
                 FROM source_records WHERE 1=1"""
        params: list[Any] = []
        if source_type:
            sql += " AND source_type = ?"
            params.append(source_type)
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        sql += " ORDER BY COALESCE(occurred_at, created_at) DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(sql, params).fetchall()
        return [
            {
                "id": r[0],
                "source_type": r[1],
                "source_ref": r[2],
                "title": r[3] or "",
                "domain": r[4] or "",
                "path": r[5] or "",
                "occurred_at": r[6] or 0,
                "created_at": r[7],
                "metadata": json.loads(r[8] or "{}"),
            }
            for r in rows
        ]

    async def start_insight_run(
        self,
        run_id: str,
        run_type: str,
        input_counts: dict | None = None,
        metadata: dict | None = None,
    ) -> None:
        if not run_id or not run_type:
            return
        async with self._lock:
            assert self._db
            self._db.execute(
                """INSERT OR REPLACE INTO insight_runs
                   (id, run_type, status, started_at, completed_at, elapsed_s,
                    input_counts, output_summary, metadata)
                   VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?)""",
                (
                    run_id,
                    run_type,
                    "running",
                    time.time(),
                    json.dumps(input_counts or {}, ensure_ascii=False),
                    "",
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            self._db.commit()

    async def finish_insight_run(
        self,
        run_id: str,
        status: str = "completed",
        output_summary: dict | str | None = None,
        metadata: dict | None = None,
    ) -> None:
        if status not in {"running", "completed", "failed"}:
            raise ValueError(f"Invalid insight run status: {status}")
        async with self._lock:
            assert self._db
            row = self._db.execute(
                "SELECT started_at, metadata FROM insight_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if not row:
                return
            now = time.time()
            existing_meta = json.loads(row[1] or "{}")
            existing_meta.update(metadata or {})
            summary = (
                output_summary
                if isinstance(output_summary, str)
                else json.dumps(output_summary or {}, ensure_ascii=False)
            )
            self._db.execute(
                """UPDATE insight_runs
                   SET status = ?, completed_at = ?, elapsed_s = ?,
                       output_summary = ?, metadata = ?
                   WHERE id = ?""",
                (
                    status,
                    now,
                    round(now - float(row[0]), 3),
                    summary,
                    json.dumps(existing_meta, ensure_ascii=False),
                    run_id,
                ),
            )
            self._db.commit()

    async def list_insight_runs(self, run_type: str | None = None, limit: int = 20) -> list[dict]:
        assert self._db
        if run_type:
            rows = self._db.execute(
                """SELECT id, run_type, status, started_at, completed_at, elapsed_s,
                          input_counts, output_summary, metadata
                   FROM insight_runs WHERE run_type = ?
                   ORDER BY started_at DESC LIMIT ?""",
                (run_type, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                """SELECT id, run_type, status, started_at, completed_at, elapsed_s,
                          input_counts, output_summary, metadata
                   FROM insight_runs ORDER BY started_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "run_type": r[1],
                "status": r[2],
                "started_at": r[3],
                "completed_at": r[4],
                "elapsed_s": r[5],
                "input_counts": json.loads(r[6] or "{}"),
                "output_summary": json.loads(r[7] or "{}") if (r[7] or "").startswith("{") else (r[7] or ""),
                "metadata": json.loads(r[8] or "{}"),
            }
            for r in rows
        ]

    async def upsert_insight_item(
        self,
        item_id: str,
        run_id: str,
        item_type: str,
        statement: str,
        source_type: str = "",
        source_ref: str = "",
        confidence: float = 0.5,
        status: str = "inferred",
        metadata: dict | None = None,
    ) -> None:
        if status not in {"inferred", "confirmed", "rejected", "hidden"}:
            raise ValueError(f"Invalid insight item status: {status}")
        statement = statement.strip()
        if not item_id or not run_id or not item_type or not statement:
            return
        async with self._lock:
            assert self._db
            existing = self._db.execute(
                "SELECT status, created_at FROM insight_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            created_at = existing[1] if existing else time.time()
            if existing and existing[0] in {"confirmed", "rejected", "hidden"}:
                status = existing[0]
            now = time.time()
            self._db.execute(
                """INSERT OR REPLACE INTO insight_items
                   (id, run_id, item_type, statement, source_type, source_ref,
                    confidence, status, created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item_id,
                    run_id,
                    item_type,
                    statement,
                    source_type,
                    source_ref,
                    float(max(0, min(1, confidence))),
                    status,
                    created_at,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            self._db.commit()

    async def list_insight_items(
        self,
        run_id: str | None = None,
        item_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        include_hidden: bool = False,
    ) -> list[dict]:
        assert self._db
        sql = """SELECT id, run_id, item_type, statement, source_type, source_ref,
                        confidence, status, created_at, updated_at, metadata
                 FROM insight_items WHERE 1=1"""
        params: list[Any] = []
        if run_id:
            sql += " AND run_id = ?"
            params.append(run_id)
        if item_type:
            sql += " AND item_type = ?"
            params.append(item_type)
        if status:
            sql += " AND status = ?"
            params.append(status)
        elif not include_hidden:
            sql += " AND status != 'hidden'"
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(sql, params).fetchall()
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
                "metadata": json.loads(r[10] or "{}"),
            }
            for r in rows
        ]

    # ── Agent context (session cache) ─────────────────────────

    async def save_context(self, session_id: str, agent_name: str, context: dict, ttl: float = 3600) -> None:
        async with self._lock:
            now = time.time()
            assert self._db
            self._db.execute(
                """INSERT OR REPLACE INTO agent_context
                   (session_id, agent_name, context, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, agent_name, json.dumps(context), now, now + ttl),
            )
            self._db.commit()

    async def load_context(self, session_id: str) -> dict | None:
        assert self._db
        row = self._db.execute(
            "SELECT context, expires_at FROM agent_context WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row and row[1] > time.time():
            return json.loads(row[0])
        return None

    # ── Stats ─────────────────────────────────────────────────

    async def count_summarized_files(self) -> int:
        assert self._db
        return self._db.execute(
            "SELECT COUNT(*) FROM file_index WHERE semantic_summary != '' AND semantic_summary IS NOT NULL"
        ).fetchone()[0]

    async def stats(self) -> dict:
        assert self._db
        file_count = self._db.execute("SELECT COUNT(*) FROM file_index").fetchone()[0]
        knowledge_count = self._db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        chunk_count = self._db.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
        return {
            "indexed_files": file_count,
            "knowledge_entries": knowledge_count,
            "document_chunks": chunk_count,
            "cache_items": len(self._cache),
        }

    async def stop(self) -> None:
        if self._db:
            self._db.close()
            self._db = None
        logger.info("Memory store closed")


def content_hash(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()[:16]
