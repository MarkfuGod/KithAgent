"""TriageAgent — verifies the rule-based / LLM-less code paths.

No pytest-asyncio: we wrap each async body in `asyncio.run`.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.agents.base import AgentTask
from src.agents.triage import TriageAgent
from src.kernel.config import EmbeddingConfig, MemoryConfig
from src.memory.store import MemoryStore


class _FakeLLM:
    """Tiny stand-in that can flip `available_providers()` on/off."""
    def __init__(self, *, has_provider: bool):
        self._has = has_provider

    def available_providers(self) -> list[str]:
        return ["fake"] if self._has else []


def _kernel_with_triage_config(**overrides) -> SimpleNamespace:
    triage = SimpleNamespace(
        skip_path_patterns=overrides.get("skip_path_patterns", []),
        file_type_priority=overrides.get("file_type_priority", {}),
        hints=overrides.get("hints", []),
    )
    cfg = SimpleNamespace(triage=triage)
    return SimpleNamespace(config=cfg)


async def _make_store(tmp_path: Path) -> MemoryStore:
    mc = MemoryConfig(
        db_path=tmp_path / "triage.db",
        cache_max_items=64,
        use_local_embeddings=False,
        local_model="",
        embedding=EmbeddingConfig(),
    )
    s = MemoryStore(mc)
    await s.initialize()
    return s


async def _seed(store: MemoryStore, paths: list[tuple[str, str]]) -> None:
    for path, ext in paths:
        await store.upsert_file(
            path=path,
            content_hash=f"h-{path}",
            size_bytes=1024,
            modified_at=time.time(),
            file_type=ext,
        )


def test_rule_based_skip_without_llm(tmp_path: Path) -> None:
    async def body() -> None:
        store = await _make_store(tmp_path)
        try:
            await _seed(store, [
                ("/u/me/proj/.venv/lib/site-packages/foo.py", ".py"),
                ("/u/me/proj/node_modules/bar/index.js", ".js"),
                ("/u/me/proj/src/main.py", ".py"),
                ("/u/me/docs/notes.md", ".md"),
            ])

            agent = TriageAgent()
            ctx: dict[str, Any] = {
                "memory": store,
                "llm": _FakeLLM(has_provider=False),
                "kernel": _kernel_with_triage_config(
                    skip_path_patterns=["site-packages/", "node_modules/"],
                ),
            }

            result = await agent.execute(AgentTask(name="triage"), ctx)

            assert result["mode"] == "rules_only"
            assert result["rule_based_skipped"] == 2
            assert result["unknown_marked"] == 2

            stats = await store.get_triage_stats()
            assert stats.get("skip", 0) == 2
            assert stats.get("unknown", 0) == 2
            assert stats.get("untriaged", 0) == 0, \
                "No file may be left untriaged after an LLM-less triage pass"
        finally:
            if store._db:
                store._db.close()

    asyncio.run(body())


def test_unknown_files_reopened_when_llm_returns(tmp_path: Path) -> None:
    async def body() -> None:
        store = await _make_store(tmp_path)
        try:
            await _seed(store, [("/u/me/proj/src/main.py", ".py")])
            agent = TriageAgent()

            offline_ctx: dict[str, Any] = {
                "memory": store,
                "llm": _FakeLLM(has_provider=False),
                "kernel": _kernel_with_triage_config(skip_path_patterns=[]),
            }
            await agent.execute(AgentTask(name="triage"), offline_ctx)
            assert (await store.get_triage_stats()).get("unknown", 0) == 1

            # LLM comes back online. _FakeLLM has no `complete`, so the LLM
            # phase will raise per-group — that's fine, we only care that
            # the 'unknown' rows get reopened up front.
            online_ctx = dict(offline_ctx)
            online_ctx["llm"] = _FakeLLM(has_provider=True)
            try:
                await agent.execute(AgentTask(name="triage"), online_ctx)
            except Exception:
                pass

            stats = await store.get_triage_stats()
            assert stats.get("unknown", 0) == 0
        finally:
            if store._db:
                store._db.close()

    asyncio.run(body())
