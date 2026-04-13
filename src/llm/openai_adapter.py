"""
OpenAI Adapter — supports GPT-4o, GPT-4.1, o3-mini, etc.

Uses the openai Python SDK if installed, falls back to raw HTTP via aiohttp.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from src.llm.base import LLMMessage, LLMProvider, LLMResponse

logger = logging.getLogger("agent_sys.llm.openai")


class OpenAIAdapter(LLMProvider):
    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str = "https://api.openai.com/v1",
        models: dict[str, str] | None = None,
    ):
        self._api_key = api_key or os.environ.get(api_key_env, "")
        self._base_url = base_url.rstrip("/")
        self._models = models or {"fast": "gpt-4o-mini", "strong": "gpt-4o"}
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
        model = model or self._models.get("fast", "gpt-4o-mini")
        msgs = [{"role": m.role, "content": m.content} for m in messages]

        # Try the openai SDK first
        try:
            return await self._call_sdk(msgs, model, temperature, max_tokens, **kwargs)
        except ImportError:
            return await self._call_http(msgs, model, temperature, max_tokens, **kwargs)

    async def _call_sdk(
        self, msgs: list[dict], model: str, temperature: float, max_tokens: int, **kwargs,
    ) -> LLMResponse:
        import openai

        if self._client is None:
            self._client = openai.AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)

        resp = await self._client.chat.completions.create(
            model=model,
            messages=msgs,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        choice = resp.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=model,
            provider=self.name,
            usage={
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
            },
            raw=resp,
        )

    async def _call_http(
        self, msgs: list[dict], model: str, temperature: float, max_tokens: int, **kwargs,
    ) -> LLMResponse:
        """Fallback: raw HTTP via aiohttp when openai SDK not installed."""
        import aiohttp

        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                data = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"OpenAI API error ({resp.status}): {data}")

        choice = data["choices"][0]
        usage = data.get("usage", {})
        return LLMResponse(
            content=choice["message"]["content"],
            model=model,
            provider=self.name,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            },
            raw=data,
        )
