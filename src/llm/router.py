"""
Model Router — the 'dispatcher' that selects which LLM provider and
model tier to use for each task type.

Analogous to how an OS routes I/O to different device drivers,
the router sends LLM tasks to the best-fit provider/model.
"""

from __future__ import annotations

import logging
from typing import Any

from src.llm.base import LLMMessage, LLMProvider, LLMResponse

logger = logging.getLogger("agent_sys.llm.router")

# Task type → model tier mapping
DEFAULT_ROUTING: dict[str, str] = {
    "summarize": "fast",
    "analyze": "strong",
    "classify": "fast",
    "report": "strong",
    "profile": "strong",
    "search": "fast",
    "vision": "vision",
}


class ModelRouter:
    """Routes LLM requests to the appropriate provider and model tier."""

    def __init__(
        self,
        providers: dict[str, LLMProvider] | None = None,
        default_provider: str = "openai",
        routing: dict[str, str] | None = None,
    ):
        self._providers: dict[str, LLMProvider] = providers or {}
        self._default_provider = default_provider
        self._routing = routing or dict(DEFAULT_ROUTING)
        self._total_calls = 0
        self._total_tokens = 0

    def register_provider(self, provider: LLMProvider) -> None:
        self._providers[provider.name] = provider
        logger.info("Registered LLM provider: %s (available=%s)", provider.name, provider.available())

    def get_provider(self, name: str | None = None) -> LLMProvider | None:
        name = name or self._default_provider
        return self._providers.get(name)

    def available_providers(self) -> list[str]:
        return [name for name, p in self._providers.items() if p.available()]

    async def complete(
        self,
        messages: list[LLMMessage],
        task_type: str = "summarize",
        provider_name: str | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        **kwargs,
    ) -> LLMResponse:
        """
        Route a completion request.

        Resolution order for provider:
          1. Explicit provider_name parameter
          2. Default provider from config

        Resolution order for model:
          1. Explicit model parameter
          2. Tier from routing table (task_type → tier → provider.models[tier])
        """
        provider = self._resolve_provider(provider_name)
        if not provider:
            raise RuntimeError(
                f"No available LLM provider. Configured: {list(self._providers.keys())}, "
                f"Available: {self.available_providers()}"
            )

        if model is None:
            tier = self._routing.get(task_type, "fast")
            model = provider.list_models().get(tier)

        logger.debug(
            "Routing [%s] → provider=%s, model=%s",
            task_type, provider.name, model,
        )

        response = await provider.complete(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

        self._total_calls += 1
        self._total_tokens += sum(response.usage.values())
        return response

    def _resolve_provider(self, name: str | None) -> LLMProvider | None:
        if name:
            p = self._providers.get(name)
            if p and p.available():
                return p

        # Try default
        p = self._providers.get(self._default_provider)
        if p and p.available():
            return p

        # Fallback: first available
        for p in self._providers.values():
            if p.available():
                return p
        return None

    def status(self) -> dict:
        return {
            "providers": {
                name: {"available": p.available(), "models": p.list_models()}
                for name, p in self._providers.items()
            },
            "default_provider": self._default_provider,
            "routing": self._routing,
            "total_calls": self._total_calls,
            "total_tokens": self._total_tokens,
        }


def create_router_from_config(llm_config: dict[str, Any]) -> ModelRouter:
    """Factory: build a ModelRouter from the 'llm' section of config YAML."""
    from src.llm.openai_adapter import OpenAIAdapter
    from src.llm.claude_adapter import AnthropicAdapter, AnthropicCompatibleAdapter
    from src.llm.compatible_adapter import OpenAICompatibleAdapter

    providers_cfg = llm_config.get("providers", {})
    router = ModelRouter(
        default_provider=llm_config.get("default_provider", "openai"),
        routing=llm_config.get("routing"),
    )

    if "openai" in providers_cfg:
        cfg = providers_cfg["openai"]
        router.register_provider(OpenAIAdapter(
            api_key_env=cfg.get("api_key_env", "OPENAI_API_KEY"),
            base_url=cfg.get("base_url", "https://api.openai.com/v1"),
            models=cfg.get("models"),
        ))

    if "anthropic" in providers_cfg:
        cfg = providers_cfg["anthropic"]
        router.register_provider(AnthropicAdapter(
            api_key_env=cfg.get("api_key_env", "ANTHROPIC_API_KEY"),
            base_url=cfg.get("base_url"),
            models=cfg.get("models"),
        ))

    if "anthropic_compatible" in providers_cfg:
        cfg = providers_cfg["anthropic_compatible"]
        router.register_provider(AnthropicCompatibleAdapter(
            base_url=cfg.get("base_url", ""),
            api_key_env=cfg.get("api_key_env", "ANTHROPIC_COMPATIBLE_API_KEY"),
            models=cfg.get("models"),
        ))

    if "openai_compatible" in providers_cfg:
        cfg = providers_cfg["openai_compatible"]
        router.register_provider(OpenAICompatibleAdapter(
            base_url=cfg.get("base_url", ""),
            api_key_env=cfg.get("api_key_env", "OPENAI_COMPATIBLE_API_KEY"),
            models=cfg.get("models"),
        ))

    return router


def check_llm_availability(llm_config: dict[str, Any]) -> list[str]:
    """Quick check: which LLM providers are available (have valid API keys)?"""
    router = create_router_from_config(llm_config)
    return router.available_providers()
