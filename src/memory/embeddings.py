"""
Embedding Engine — pluggable providers for vector semantic search.

Providers:
  - local:     sentence-transformers (all-MiniLM-L6-v2, 384d, runs on CPU)
  - dashscope: Qwen3-VL-Embedding via DashScope OpenAI-compatible API
  - openai:    OpenAI text-embedding-3-small/large

Configured via memory.embedding in default.yaml.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("agent_sys.memory.embeddings")

_provider_instance: EmbeddingProvider | None = None


class EmbeddingProvider:
    """Base class for embedding providers."""
    name: str = "base"

    def available(self) -> bool:
        return False

    def embed_one(self, text: str) -> bytes | None:
        raise NotImplementedError

    def embed_batch(self, texts: list[str]) -> list[bytes]:
        raise NotImplementedError


class LocalEmbeddingProvider(EmbeddingProvider):
    """sentence-transformers local model."""
    name = "local"

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            import os
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
            logger.info("Loaded local embedding model: %s", self._model_name)
        return self._model

    def available(self) -> bool:
        try:
            from sentence_transformers import SentenceTransformer  # noqa: F401
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.warning("sentence-transformers available check failed: %s", e)
            return False

    def embed_one(self, text: str) -> bytes | None:
        try:
            model = self._load()
            vec = model.encode(text, normalize_embeddings=True)
            return numpy_to_bytes(vec)
        except Exception as e:
            logger.warning("Local embedding failed: %s", e)
            return None

    def embed_batch(self, texts: list[str]) -> list[bytes]:
        try:
            model = self._load()
            vecs = model.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
            return [numpy_to_bytes(v) for v in vecs]
        except Exception as e:
            logger.warning("Local batch embedding failed: %s", e)
            return []


class APIEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible embedding API (works with DashScope, OpenAI, etc.)."""

    def __init__(
        self,
        name: str = "openai",
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str = "https://api.openai.com/v1",
        model: str = "text-embedding-3-small",
        dimensions: int = 0,
    ):
        self.name = name
        self._api_key_env = api_key_env
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimensions = dimensions
        self._client = None

    def _get_key(self) -> str | None:
        return os.environ.get(self._api_key_env)

    def available(self) -> bool:
        return bool(self._get_key()) and bool(self._base_url)

    def _get_client(self):
        if self._client is None:
            try:
                import httpx
            except ImportError:
                import urllib.request
                self._client = "urllib"
                return self._client
            self._client = httpx.Client(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._get_key()}"},
                timeout=30.0,
            )
        return self._client

    def _call_api(self, texts: list[str]) -> list[list[float]]:
        """Call the OpenAI-compatible embeddings endpoint."""
        import json as _json

        key = self._get_key()
        if not key:
            raise RuntimeError(f"No API key set in env var {self._api_key_env}")

        body: dict[str, Any] = {
            "input": texts,
            "model": self._model,
        }
        if self._dimensions > 0:
            body["dimensions"] = self._dimensions

        client = self._get_client()
        if client == "urllib":
            import urllib.request
            req = urllib.request.Request(
                f"{self._base_url}/embeddings",
                data=_json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = _json.loads(resp.read())
        else:
            resp = client.post("/embeddings", json=body)
            resp.raise_for_status()
            data = resp.json()

        embeddings = sorted(data["data"], key=lambda x: x["index"])
        return [e["embedding"] for e in embeddings]

    def embed_one(self, text: str) -> bytes | None:
        try:
            import numpy as np
            vecs = self._call_api([text])
            if vecs:
                arr = np.array(vecs[0], dtype=np.float32)
                arr = arr / (np.linalg.norm(arr) + 1e-9)
                return numpy_to_bytes(arr)
        except Exception as e:
            logger.warning("API embedding failed (%s): %s", self.name, e)
        return None

    def embed_batch(self, texts: list[str]) -> list[bytes]:
        try:
            import numpy as np
            all_vecs: list[bytes] = []
            batch_size = 20
            for i in range(0, len(texts), batch_size):
                chunk = texts[i:i + batch_size]
                vecs = self._call_api(chunk)
                for v in vecs:
                    arr = np.array(v, dtype=np.float32)
                    arr = arr / (np.linalg.norm(arr) + 1e-9)
                    all_vecs.append(numpy_to_bytes(arr))
            return all_vecs
        except Exception as e:
            logger.warning("API batch embedding failed (%s): %s", self.name, e)
            return []


def configure(provider: str = "local", **kwargs) -> None:
    """Initialize the global embedding provider from config."""
    global _provider_instance

    if provider == "local":
        model_name = kwargs.get("local_model", "all-MiniLM-L6-v2")
        _provider_instance = LocalEmbeddingProvider(model_name)
        logger.info("Embedding provider: local (%s)", model_name)

    elif provider == "dashscope":
        _provider_instance = APIEmbeddingProvider(
            name="dashscope",
            api_key_env=kwargs.get("api_key_env", "DASHSCOPE_API_KEY"),
            base_url=kwargs.get("api_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            model=kwargs.get("model", "qwen3-vl-embedding"),
            dimensions=kwargs.get("dimensions", 0),
        )
        logger.info("Embedding provider: dashscope (%s)", _provider_instance._model)

    elif provider == "openai":
        _provider_instance = APIEmbeddingProvider(
            name="openai",
            api_key_env=kwargs.get("api_key_env", "OPENAI_API_KEY"),
            base_url=kwargs.get("api_base_url", "https://api.openai.com/v1"),
            model=kwargs.get("model", "text-embedding-3-small"),
            dimensions=kwargs.get("dimensions", 0),
        )
        logger.info("Embedding provider: openai (%s)", _provider_instance._model)

    else:
        logger.warning("Unknown embedding provider '%s', falling back to local", provider)
        _provider_instance = LocalEmbeddingProvider(kwargs.get("local_model", "all-MiniLM-L6-v2"))


def _get_provider() -> EmbeddingProvider | None:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = LocalEmbeddingProvider()
    try:
        if not _provider_instance.available():
            return None
    except Exception as e:
        logger.warning("Embedding provider availability check failed: %s", e)
        return None
    return _provider_instance


def get_provider_info() -> dict[str, Any]:
    """Return info about the current embedding provider for dashboard display."""
    global _provider_instance
    if _provider_instance is None:
        return {"provider": "none", "available": False}
    info: dict[str, Any] = {"provider": _provider_instance.name}
    try:
        info["available"] = _provider_instance.available()
    except Exception:
        info["available"] = False
    if isinstance(_provider_instance, LocalEmbeddingProvider):
        info["model"] = _provider_instance._model_name
    elif isinstance(_provider_instance, APIEmbeddingProvider):
        info["model"] = _provider_instance._model
        info["base_url"] = _provider_instance._base_url
    return info


def is_available() -> bool:
    p = _get_provider()
    return p is not None and p.available()


def embed_text(text: str) -> bytes | None:
    p = _get_provider()
    if p is None:
        return None
    return p.embed_one(text)


def embed_texts(texts: list[str]) -> list[bytes]:
    p = _get_provider()
    if p is None:
        return []
    return p.embed_batch(texts)


def cosine_similarity(a_bytes: bytes, b_bytes: bytes) -> float:
    import numpy as np
    a = bytes_to_numpy(a_bytes)
    b = bytes_to_numpy(b_bytes)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm < 1e-9:
        return 0.0
    return float(np.dot(a, b) / norm)


def numpy_to_bytes(vec: Any) -> bytes:
    import numpy as np
    arr = np.asarray(vec, dtype=np.float32)
    return arr.tobytes()


def bytes_to_numpy(raw: bytes) -> Any:
    import numpy as np
    return np.frombuffer(raw, dtype=np.float32)
