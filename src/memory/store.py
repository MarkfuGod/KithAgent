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
import sqlite3
import time
from collections import OrderedDict
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
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
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

            CREATE INDEX IF NOT EXISTS idx_file_type ON file_index(file_type);
            CREATE INDEX IF NOT EXISTS idx_knowledge_cat ON knowledge(category);
            CREATE INDEX IF NOT EXISTS idx_agent_ctx_name ON agent_context(agent_name);
        """)

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
        async with self._lock:
            assert self._db
            self._db.execute(
                """INSERT OR REPLACE INTO file_index
                   (path, hash, size_bytes, modified_at, indexed_at, file_type, summary, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (path, content_hash, size_bytes, modified_at, time.time(),
                 file_type, summary, json.dumps(metadata or {})),
            )
            self._db.commit()
            self._cache.put(f"file:{path}", {
                "hash": content_hash, "summary": summary,
                "file_type": file_type, "size": size_bytes,
            })

    async def get_file_info(self, path: str) -> dict | None:
        cached = self._cache.get(f"file:{path}")
        if cached:
            return cached
        assert self._db
        row = self._db.execute(
            "SELECT * FROM file_index WHERE path = ?", (path,)
        ).fetchone()
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
            except Exception:
                pass

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

    async def get_files_needing_summary(self, limit: int = 50) -> list[dict]:
        """Return files that need summarization, prioritized by triage importance.

        Order: triage_status='high' first, then 'medium', then untriaged.
        Files marked 'skip' or 'low' are excluded.
        Within each tier, diversify across code/doc/image categories.
        """
        assert self._db

        _code_ext = ("'.py'","'.js'","'.ts'","'.go'","'.rs'","'.sh'","'.java'","'.c'","'.cpp'","'.swift'",
                      "'.json'","'.yaml'","'.yml'","'.toml'","'.xml'","'.csv'")
        _doc_ext = ("'.pdf'","'.docx'","'.doc'","'.pptx'","'.xlsx'","'.md'","'.txt'","'.rst'","'.tex'")
        _img_ext = ("'.png'","'.jpg'","'.jpeg'","'.gif'","'.webp'","'.bmp'")

        code_limit = max(limit // 2, 1)
        doc_limit = max(limit // 4, 1)
        img_limit = max(limit // 4, 1)

        # triage_rank: 'high'=0, 'medium'=1, untriaged=2 (skip/low excluded)
        triage_filter = "AND triage_status NOT IN ('skip', 'low')"
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
                f"""SELECT path, hash, file_type, size_bytes, summary, modified_at
                    FROM file_index
                    WHERE (semantic_summary = '' OR semantic_summary IS NULL)
                      AND file_type IN ({ext_list})
                      {triage_filter}
                    ORDER BY {triage_order}, priority ASC, modified_at DESC
                    LIMIT ?""",
                (grp_limit,),
            ).fetchall()
            results.extend(rows)

        results.sort(key=lambda r: (r[3] or 0), reverse=True)

        return [
            {"path": r[0], "hash": r[1], "file_type": r[2], "size_bytes": r[3],
             "summary": r[4], "modified_at": r[5]}
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
        async with self._lock:
            assert self._db
            self._db.executemany(
                "UPDATE file_index SET embedding = ?, embedding_model = ? WHERE path = ?",
                [(emb, model, path) for path, emb, model in updates],
            )
            self._db.commit()

    async def get_files_needing_embedding(self, limit: int = 200) -> list[dict]:
        """Return files that have summaries but no embeddings yet (high/medium triage only)."""
        assert self._db
        rows = self._db.execute(
            """SELECT path, semantic_summary
               FROM file_index
               WHERE semantic_summary != '' AND semantic_summary IS NOT NULL
                 AND (embedding IS NULL OR embedding = '')
                 AND triage_status IN ('high', 'medium', '')
               ORDER BY
                 CASE triage_status WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                 modified_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [{"path": r[0], "semantic_summary": r[1]} for r in rows]

    async def vector_search(self, query_embedding: bytes, limit: int = 20) -> list[dict]:
        """Find files by cosine similarity to query embedding."""
        assert self._db
        from src.memory.embeddings import cosine_similarity

        rows = self._db.execute(
            """SELECT path, file_type, semantic_summary, size_bytes, priority, embedding
               FROM file_index
               WHERE embedding IS NOT NULL AND embedding != ''"""
        ).fetchall()

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
        async with self._lock:
            assert self._db
            self._db.executemany(
                "UPDATE file_index SET priority = ? WHERE path = ?",
                [(p, path) for path, p in updates],
            )
            self._db.commit()

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

    # ── Triage operations ──────────────────────────────────────

    async def get_untriaged_files(self, limit: int = 500) -> list[dict]:
        """Return files that haven't been triaged yet, grouped to give LLM directory context."""
        assert self._db
        rows = self._db.execute(
            """SELECT path, file_type, size_bytes, modified_at
               FROM file_index
               WHERE (triage_status = '' OR triage_status IS NULL)
               ORDER BY modified_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
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
        async with self._lock:
            assert self._db
            self._db.executemany(
                "UPDATE file_index SET triage_status = ? WHERE path = ?",
                [(status, path) for path, status in updates],
            )
            self._db.commit()

    async def batch_update_triage_by_prefix(self, prefix: str, status: str) -> int:
        """Mark all files under a directory prefix with the given triage status.
        Returns the number of rows affected."""
        async with self._lock:
            assert self._db
            cursor = self._db.execute(
                "UPDATE file_index SET triage_status = ? WHERE path LIKE ? AND (triage_status = '' OR triage_status IS NULL)",
                (status, f"{prefix}%"),
            )
            self._db.commit()
            return cursor.rowcount

    async def remove_file(self, path: str) -> None:
        async with self._lock:
            assert self._db
            self._db.execute("DELETE FROM file_index WHERE path = ?", (path,))
            self._db.commit()
            self._cache.invalidate(f"file:{path}")

    # ── Knowledge operations ──────────────────────────────────

    async def store_knowledge(
        self, kid: str, category: str, content: str,
        source_path: str = "", metadata: dict | None = None,
    ) -> None:
        async with self._lock:
            now = time.time()
            assert self._db
            self._db.execute(
                """INSERT OR REPLACE INTO knowledge
                   (id, category, content, source_path, created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (kid, category, content, source_path, now, now, json.dumps(metadata or {})),
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
        return {
            "indexed_files": file_count,
            "knowledge_entries": knowledge_count,
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
