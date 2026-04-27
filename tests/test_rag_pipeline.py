import asyncio

from src.agents.assistant import AssistantAgent
from src.agents.rag_indexer import RagIndexerAgent
from src.agents.base import AgentTask
from src.kernel.config import MemoryConfig
from src.memory.chunking import chunk_text
from src.memory.embeddings import APIEmbeddingProvider
from src.memory.store import MemoryStore, content_hash


class _Kernel:
    def __init__(self, memory_config):
        self.config = type("Config", (), {"memory": memory_config})()


class _FakeEmbeddingResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "output": {
                "embeddings": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ],
            },
        }


class _FakeEmbeddingClient:
    def __init__(self):
        self.url = None
        self.body = None

    def post(self, url, json):
        self.url = url
        self.body = json
        return _FakeEmbeddingResponse()


def test_qwen_vl_embedding_uses_dashscope_multimodal_endpoint(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    provider = APIEmbeddingProvider(
        name="dashscope",
        api_key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3-vl-embedding",
    )
    client = _FakeEmbeddingClient()
    provider._client = client

    vectors = provider._call_api(["alpha", "beta"])

    assert client.url == (
        "https://dashscope.aliyuncs.com/api/v1/services/embeddings/"
        "multimodal-embedding/multimodal-embedding"
    )
    assert client.body["input"]["contents"] == [{"text": "alpha"}, {"text": "beta"}]
    assert client.body["parameters"]["dimension"] == 1024
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]


def test_qwen_vl_structured_embedding_accepts_image_input(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    provider = APIEmbeddingProvider(
        name="dashscope",
        api_key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3-vl-embedding",
    )
    client = _FakeEmbeddingClient()
    provider._client = client

    vectors = provider.embed_items([
        {"text": "screenshot with roadmap", "image": "data:image/png;base64,AAAA"},
        {"text": "plain caption"},
    ])

    assert client.body["input"]["contents"][0] == {
        "text": "screenshot with roadmap",
        "image": "data:image/png;base64,AAAA",
    }
    assert client.body["input"]["contents"][1] == {"text": "plain caption"}
    assert len(vectors) == 2


def test_chunk_text_preserves_line_ranges_and_overlap():
    text = "\n".join(f"line {i} about project alpha" for i in range(1, 25))
    chunks = chunk_text(text, path="/tmp/notes.md", chunk_size_chars=110, chunk_overlap_chars=35)

    assert len(chunks) > 1
    assert chunks[0]["start_line"] == 1
    assert chunks[0]["end_line"] >= chunks[1]["start_line"]
    assert "project alpha" in chunks[0]["content"]


def test_memory_store_chunk_fts_search_returns_citations(tmp_path):
    async def run_case():
        store = MemoryStore(MemoryConfig(db_path=tmp_path / "memory.db"))
        await store.initialize()
        try:
            path = str(tmp_path / "alpha.md")
            await store.upsert_file(
                path=path,
                content_hash=content_hash("alpha roadmap and launch notes"),
                size_bytes=32,
                modified_at=123.0,
                file_type=".md",
                summary="alpha",
            )
            await store.batch_update_triage([(path, "high")])
            await store.upsert_document_chunks(
                path,
                content_hash("alpha roadmap and launch notes"),
                [{
                    "chunk_index": 0,
                    "start_line": 1,
                    "end_line": 2,
                    "content": "The alpha roadmap focuses on launch notes and user onboarding.",
                }],
            )

            results = await store.hybrid_search_chunks("alpha launch onboarding", limit=3)
            assert results
            assert results[0]["source_id"] == "S1"
            assert results[0]["path"] == path
            assert "onboarding" in results[0]["content"]
        finally:
            await store.stop()

    asyncio.run(run_case())


def test_rag_indexer_builds_chunks_without_blocking_on_embeddings(tmp_path):
    async def run_case():
        cfg = MemoryConfig(db_path=tmp_path / "memory.db")
        cfg.rag.batch_size = 5
        cfg.rag.embedding_batch_size = 0
        store = MemoryStore(cfg)
        await store.initialize()
        try:
            path = tmp_path / "alpha.md"
            path.write_text("Alpha project\n\nThe launch plan includes onboarding checklists.\n")
            await store.upsert_file(
                path=str(path),
                content_hash=content_hash(path.read_text()),
                size_bytes=path.stat().st_size,
                modified_at=path.stat().st_mtime,
                file_type=".md",
                summary="alpha",
            )
            await store.batch_update_triage([(str(path), "high")])

            result = await RagIndexerAgent().execute(
                AgentTask(name="rag_indexer", input_data={"embedding_batch_size": 0}),
                {"memory": store, "kernel": _Kernel(cfg)},
            )
            assert result["indexed_files"] == 1
            assert await store.count_document_chunks() >= 1
        finally:
            await store.stop()

    asyncio.run(run_case())


def test_rag_indexer_builds_media_chunk_from_image_summary(tmp_path):
    async def run_case():
        cfg = MemoryConfig(db_path=tmp_path / "memory.db")
        cfg.rag.batch_size = 5
        cfg.rag.embedding_batch_size = 0
        store = MemoryStore(cfg)
        await store.initialize()
        try:
            path = tmp_path / "roadmap.png"
            path.write_bytes(b"fake-png")
            await store.upsert_file(
                path=str(path),
                content_hash=content_hash(path.read_bytes()),
                size_bytes=path.stat().st_size,
                modified_at=path.stat().st_mtime,
                file_type=".png",
                summary="roadmap screenshot with onboarding funnel",
            )
            await store.batch_update_triage([(str(path), "high")])

            result = await RagIndexerAgent().execute(
                AgentTask(name="rag_indexer", input_data={"embedding_batch_size": 0}),
                {"memory": store, "kernel": _Kernel(cfg)},
            )
            assert result["indexed_files"] == 1

            results = await store.hybrid_search_chunks("onboarding funnel", limit=3)
            assert results
            assert results[0]["metadata"]["modality"] == "image"
            assert results[0]["metadata"]["source_kind"] == "image"
            assert results[0]["start_line"] is None
            assert "roadmap screenshot" in results[0]["content"]
        finally:
            await store.stop()

    asyncio.run(run_case())


def test_rag_indexer_media_embedding_input_includes_image(tmp_path):
    path = tmp_path / "screen.png"
    path.write_bytes(b"image-bytes")
    item = RagIndexerAgent()._embedding_input_for_chunk({
        "path": str(path),
        "content": "screen summary",
        "metadata": {"modality": "image", "source_kind": "image"},
    })

    assert item["text"] == "screen summary"
    assert item["image"].startswith("data:image/png;base64,")


def test_assistant_retrieves_rag_evidence_best_effort(tmp_path):
    async def run_case():
        cfg = MemoryConfig(db_path=tmp_path / "memory.db")
        store = MemoryStore(cfg)
        await store.initialize()
        try:
            path = str(tmp_path / "focus.md")
            await store.upsert_file(
                path=path,
                content_hash=content_hash("focus rituals"),
                size_bytes=32,
                modified_at=123.0,
                file_type=".md",
                summary="focus",
            )
            await store.batch_update_triage([(path, "high")])
            await store.upsert_document_chunks(
                path,
                content_hash("focus rituals"),
                [{"chunk_index": 0, "start_line": 1, "end_line": 1, "content": "Focus rituals include morning planning."}],
            )

            evidence = await AssistantAgent()._retrieve_rag_evidence(
                AgentTask(name="assistant", input_data={}),
                {"memory": store, "kernel": _Kernel(cfg)},
                "morning planning focus",
            )
            assert evidence
            assert evidence[0]["source_id"] == "S1"
            assert "morning planning" in evidence[0]["snippet"]
        finally:
            await store.stop()

    asyncio.run(run_case())
