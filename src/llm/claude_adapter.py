"""
Claude Adapter — supports Sonnet, Opus via the Anthropic Messages API.

Uses the anthropic SDK if installed, falls back to raw HTTP via aiohttp.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from src.llm.base import LLMMessage, LLMProvider, LLMResponse

logger = logging.getLogger("agent_sys.llm.claude")

_DEFAULT_ANTHROPIC_VERSION = "2023-06-01"


class ClaudeAdapter(LLMProvider):
    name = "claude"

    def __init__(
        self,
        api_key: str | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        models: dict[str, str] | None = None,
    ):
        self._api_key = api_key or os.environ.get(api_key_env, "")
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

        # Separate system message from conversation (Anthropic requires it as a top-level param)
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
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)

        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            params["system"] = system

        resp = await self._client.messages.create(**params)
        content = resp.content[0].text if resp.content else ""
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

        url = "https://api.anthropic.com/v1/messages"
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
                    raise RuntimeError(f"Claude API error ({resp.status}): {data}")

        content_block = data.get("content", [{}])[0]
        usage = data.get("usage", {})
        return LLMResponse(
            content=content_block.get("text", ""),
            model=model,
            provider=self.name,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
            },
            raw=data,
        )
