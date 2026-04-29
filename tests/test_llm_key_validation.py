from __future__ import annotations

import pytest

from src.llm.base import LLMMessage
from src.llm.compatible_adapter import OpenAICompatibleAdapter
from src.llm.openai_adapter import OpenAIAdapter


def test_openai_adapter_disables_non_ascii_header_key() -> None:
    adapter = OpenAIAdapter(api_key="不是key", api_key_env="OPENAI_API_KEY")

    assert adapter.available() is False


def test_openai_compatible_adapter_disables_non_ascii_header_key() -> None:
    adapter = OpenAICompatibleAdapter(
        base_url="https://example.com/v1",
        api_key="不是key",
        api_key_env="OPENAI_COMPATIBLE_API_KEY",
    )

    assert adapter.available() is False


@pytest.mark.asyncio
async def test_non_ascii_header_key_fails_before_http_call() -> None:
    adapter = OpenAIAdapter(api_key="不是key", api_key_env="OPENAI_API_KEY")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await adapter.complete([LLMMessage(role="user", content="hello")])
