"""Smoke + regression tests for MemoryStore.

Uses plain `asyncio.run` so no pytest-asyncio is required.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from src.kernel.config import EmbeddingConfig, MemoryConfig
from src.memory.store import MemoryStore


def _make_config(tmp_path: Path) -> MemoryConfig:
    return MemoryConfig(
        db_path=tmp_path / "memory.db",
        cache_max_items=128,
        use_local_embeddings=False,
        local_model="",
        embedding=EmbeddingConfig(),
    )


def _run(coro):
    return asyncio.run(coro)


async def _new_store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(_make_config(tmp_path))
    await s.initialize()
    return s


def _close(store: MemoryStore) -> None:
    if store._db is not None:
        store._db.close()


async def _insert(store: MemoryStore, path: str, *, file_type: str = ".py",
                  size: int = 1024, modified_at: float | None = None) -> None:
    await store.upsert_file(
        path=path,
        content_hash=f"hash-{path}",
        size_bytes=size,
        modified_at=modified_at if modified_at is not None else time.time(),
        file_type=file_type,
    )


def test_upsert_and_get_roundtrip(tmp_path: Path) -> None:
    async def body() -> None:
        store = await _new_store(tmp_path)
        try:
            await _insert(store, "/u/me/project/a.py", file_type=".py", size=42)
            info = await store.get_file_info("/u/me/project/a.py")
            assert info is not None
            assert info.get("hash") == "hash-/u/me/project/a.py"
        finally:
            _close(store)

    _run(body())


def test_batch_update_triage_by_prefix_only_touches_untriaged(tmp_path: Path) -> None:
    async def body() -> None:
        store = await _new_store(tmp_path)
        try:
            await _insert(store, "/u/me/project/venv/lib/a.py")
            await _insert(store, "/u/me/project/venv/lib/b.py")
            await _insert(store, "/u/me/project/src/main.py")
            await store.batch_update_triage([("/u/me/project/src/main.py", "high")])

            changed = await store.batch_update_triage_by_prefix("%venv/lib/", "skip")
            assert changed == 2
            # Second run: nothing left to update.
            assert await store.batch_update_triage_by_prefix("%venv/lib/", "skip") == 0
        finally:
            _close(store)

    _run(body())


def test_park_remaining_as_unknown(tmp_path: Path) -> None:
    async def body() -> None:
        store = await _new_store(tmp_path)
        try:
            await _insert(store, "/u/me/a.py")
            await _insert(store, "/u/me/b.py")
            await store.batch_update_triage([("/u/me/a.py", "high")])

            parked = await store.park_remaining_as_unknown()
            assert parked == 1

            stats = await store.get_triage_stats()
            assert stats.get("unknown", 0) == 1
            assert stats.get("high", 0) == 1
        finally:
            _close(store)

    _run(body())


def test_reopen_unknown_clears_status(tmp_path: Path) -> None:
    async def body() -> None:
        store = await _new_store(tmp_path)
        try:
            await _insert(store, "/u/me/c.py")
            await store.park_remaining_as_unknown()
            reopened = await store.reopen_unknown()
            assert reopened == 1
            stats = await store.get_triage_stats()
            assert stats.get("unknown", 0) == 0
            assert stats.get("untriaged", 0) == 1
        finally:
            _close(store)

    _run(body())


def test_get_files_needing_summary_skips_unknown(tmp_path: Path) -> None:
    async def body() -> None:
        store = await _new_store(tmp_path)
        try:
            now = time.time()
            await _insert(store, "/u/me/high.py", modified_at=now - 1)
            await _insert(store, "/u/me/medium.py", modified_at=now - 2)
            await _insert(store, "/u/me/unknown.py", modified_at=now)

            await store.batch_update_triage([
                ("/u/me/high.py", "high"),
                ("/u/me/medium.py", "medium"),
                ("/u/me/unknown.py", "unknown"),
            ])

            rows = await store.get_files_needing_summary(limit=10)
            paths = [r["path"] for r in rows]

            assert "/u/me/unknown.py" not in paths, \
                "unknown-triaged files must be excluded"
            assert paths.index("/u/me/high.py") < paths.index("/u/me/medium.py"), \
                "triage_status ordering must dominate modified_at"
        finally:
            _close(store)

    _run(body())


def test_prune_out_of_scope_marks_rows_outside_watch_paths(tmp_path: Path) -> None:
    async def body() -> None:
        store = await _new_store(tmp_path)
        try:
            # Inside the (fake) new scope:
            await _insert(store, str(tmp_path / "docs/note.md"), file_type=".md")
            # Outside — these used to be scanned but the user narrowed scope:
            await _insert(store, "/opt/legacy/stale.py")
            await _insert(store, "/other/home/.cursor/extensions/foo.png",
                          file_type=".png")

            changed = await store.prune_out_of_scope([str(tmp_path / "docs")])
            assert changed == 2

            stats = await store.get_triage_stats()
            assert stats.get("skip", 0) == 2

            # Already-skip rows must not be double-counted on re-run.
            assert await store.prune_out_of_scope([str(tmp_path / "docs")]) == 0

            # And an in-scope row was left untouched (still untriaged).
            assert stats.get("untriaged", 0) == 1
        finally:
            _close(store)

    _run(body())


def test_search_files_fallback_without_vectors(tmp_path: Path) -> None:
    async def body() -> None:
        store = await _new_store(tmp_path)
        try:
            await _insert(store, "/u/me/redis_config.md", file_type=".md")
            await _insert(store, "/u/me/random.py", file_type=".py")

            hits = await store.search_files("redis", limit=5)
            assert any("redis_config.md" in h["path"] for h in hits)
        finally:
            _close(store)

    _run(body())
