"""
Anthropic Adapter — supports Claude Sonnet/Opus via the Anthropic Messages API,
and any Anthropic-compatible third-party endpoint (MiniMax, etc.).

Uses the anthropic SDK if installed, falls back to raw HTTP via aiohttp.
Handles extended thinking models that return thinking+text content blocks.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from src.llm.base import LLMMessage, LLMProvider, LLMResponse

logger = logging.getLogger("agent_sys.llm.anthropic")

_DEFAULT_ANTHROPIC_VERSION = "2023-06-01"


def _extract_text_from_content(content_blocks: list) -> str:
    """Extract text from Anthropic content blocks, skipping thinking blocks."""
    for block in content_blocks:
        if isinstance(block, dict):
            if block.get("type") == "text":
                return block.get("text", "")
        elif hasattr(block, "type") and block.type == "text":
            return block.text
    # Fallback: grab first block's text if no explicit text type found
    if content_blocks:
        first = content_blocks[0]
        if isinstance(first, dict):
            return first.get("text", first.get("thinking", ""))
        return getattr(first, "text", getattr(first, "thinking", ""))
    return ""


class AnthropicAdapter(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        base_url: str | None = None,
        models: dict[str, str] | None = None,
    ):
        self._api_key = api_key or os.environ.get(api_key_env, "")
        self._base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL", "")
        self._models = models or {
            "fast": "claude-sonnet-4-20250514",
            "strong": "claude-opus-4-20250514",
        }
        self._client = None

    def available(self) -> bool:
        return bool(self._api_key)

    def list_models(self) -> dict[str, str]:
        return dict(self._models)

    async def complete(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        **kwargs,
    ) -> LLMResponse:
        model = model or self._models.get("fast", "claude-sonnet-4-20250514")

        system_text = ""
        conv_messages: list[dict[str, str]] = []
        for m in messages:
            if m.role == "system":
                system_text += m.content + "\n"
            else:
                conv_messages.append({"role": m.role, "content": m.content})

        if not conv_messages:
            conv_messages = [{"role": "user", "content": "Hello"}]

        try:
            return await self._call_sdk(
                conv_messages, system_text.strip(), model, temperature, max_tokens, **kwargs,
            )
        except ImportError:
            return await self._call_http(
                conv_messages, system_text.strip(), model, temperature, max_tokens, **kwargs,
            )

    async def _call_sdk(
        self,
        messages: list[dict],
        system: str,
        model: str,
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> LLMResponse:
        import anthropic

        if self._client is None:
            kwargs_client: dict[str, Any] = {"api_key": self._api_key}
            if self._base_url:
                kwargs_client["base_url"] = self._base_url
            self._client = anthropic.AsyncAnthropic(**kwargs_client)

        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            params["system"] = system

        resp = await self._client.messages.create(**params)
        content = _extract_text_from_content(resp.content) if resp.content else ""
        return LLMResponse(
            content=content,
            model=model,
            provider=self.name,
            usage={
                "prompt_tokens": resp.usage.input_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.output_tokens if resp.usage else 0,
            },
            raw=resp,
        )

    async def _call_http(
        self,
        messages: list[dict],
        system: str,
        model: str,
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> LLMResponse:
        import aiohttp

        base = self._base_url.rstrip("/") if self._base_url else "https://api.anthropic.com"
        url = f"{base}/v1/messages"
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _DEFAULT_ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            payload["system"] = system

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                data = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"Anthropic API error ({resp.status}): {data}")

        content = _extract_text_from_content(data.get("content", []))
        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            model=model,
            provider=self.name,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
            },
            raw=data,
        )


# Backward compat alias
ClaudeAdapter = AnthropicAdapter


class AnthropicCompatibleAdapter(AnthropicAdapter):
    """Anthropic API-compatible endpoint (MiniMax, custom proxies, etc.)."""

    name = "anthropic_compatible"

    def __init__(
        self,
        base_url: str = "",
        api_key: str | None = None,
        api_key_env: str = "ANTHROPIC_COMPATIBLE_API_KEY",
        models: dict[str, str] | None = None,
    ):
        resolved_url = base_url or os.environ.get("ANTHROPIC_COMPATIBLE_BASE_URL", "")
        super().__init__(
            api_key=api_key,
            api_key_env=api_key_env,
            base_url=resolved_url,
            models=models or {"fast": "MiniMax-M2.7", "strong": "MiniMax-M2.7"},
        )

    def available(self) -> bool:
        return bool(self._api_key) and bool(self._base_url)
