"""
Model Router — the 'dispatcher' that selects which LLM provider and
model tier to use for each task type.

Supports per-function provider+tier overrides and separate text/vision
routing, so you can use Anthropic for text triage and OpenAI for vision
summarization without changing calling code.
"""

from __future__ import annotations

import logging
from typing import Any

from src.llm.base import LLMMessage, LLMProvider, LLMResponse

logger = logging.getLogger("agent_sys.llm.router")

DEFAULT_ROUTING: dict[str, str] = {
    "summarize": "fast",
    "analyze": "strong",
    "classify": "fast",
    "report": "strong",
    "profile": "strong",
    "search": "fast",
    "vision": "vision",
}

# Maps internal task_type strings to user-facing function config names.
# Allows YAML to use friendly names (triage, brief) while code uses
# internal names (classify, etc.).
TASK_TYPE_ALIASES: dict[str, str] = {
    "classify": "triage",
}


_CIRCUIT_BREAKER_THRESHOLD = 3
_CIRCUIT_BREAKER_COOLDOWN = 300  # seconds


class ModelRouter:
    """Routes LLM requests to the appropriate provider and model tier.

    Resolution order (from most to least specific):
      1. Explicit provider_name / model parameters in complete()
      2. Per-function config (functions[task_type])
      3. Global defaults (defaults.text_provider / vision_provider)
      4. Legacy routing table (task_type -> tier on default_provider)
    """

    def __init__(
        self,
        providers: dict[str, LLMProvider] | None = None,
        default_provider: str = "openai",
        routing: dict[str, str] | None = None,
        defaults: dict[str, str] | None = None,
        functions: dict[str, dict[str, str]] | None = None,
    ):
        self._providers: dict[str, LLMProvider] = providers or {}
        self._default_provider = default_provider
        self._routing = routing or dict(DEFAULT_ROUTING)
        self._defaults = defaults or {}
        self._functions = functions or {}
        self._total_calls = 0
        self._total_tokens = 0
        self._event_bus = None
        self._auth_failures: dict[str, int] = {}
        self._tripped_until: dict[str, float] = {}

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus

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
        is_vision: bool = False,
        provider_name: str | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        **kwargs,
    ) -> LLMResponse:
        """Route a completion request with per-function provider/model resolution.

        Resolution order for provider + model:
          1. Explicit provider_name / model parameters
          2. Per-function config (self._functions[task_type])
          3. Global defaults (self._defaults)
          4. Legacy routing table (self._routing)
        """
        import time as _time

        resolved_provider_name, resolved_model = self._resolve_function_routing(
            task_type, is_vision, provider_name, model,
        )

        provider = self._resolve_provider(resolved_provider_name)
        if not provider:
            raise RuntimeError(
                f"No available LLM provider. Configured: {list(self._providers.keys())}, "
                f"Available: {self.available_providers()}"
            )

        # Circuit breaker: skip provider if recently tripped on auth failures
        tripped_ts = self._tripped_until.get(provider.name, 0)
        if _time.time() < tripped_ts:
            remaining = int(tripped_ts - _time.time())
            raise RuntimeError(
                f"Circuit breaker OPEN for provider '{provider.name}' "
                f"({self._auth_failures.get(provider.name, 0)} consecutive auth failures). "
                f"Retrying in {remaining}s. Check your API key."
            )

        if is_vision and resolved_provider_name and provider.name != resolved_provider_name:
            raise RuntimeError(
                f"Vision provider '{resolved_provider_name}' is not available (no API key?). "
                f"Refusing to fall back to '{provider.name}' which may not support image_url content. "
                f"Set a valid API key for '{resolved_provider_name}' or change defaults.vision_provider in config."
            )

        if resolved_model is None:
            tier = self._routing.get(task_type, "fast")
            resolved_model = provider.list_models().get(tier)

        logger.debug(
            "Routing [%s] (vision=%s) → provider=%s, model=%s",
            task_type, is_vision, provider.name, resolved_model,
        )

        _req_start = _time.time()
        if self._event_bus:
            await self._event_bus.emit_dict("llm.request", {
                "task_type": task_type,
                "is_vision": is_vision,
                "provider": provider.name,
                "model": resolved_model or "default",
                "max_tokens": max_tokens,
            })

        try:
            response = await provider.complete(
                messages=messages,
                model=resolved_model,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
        except Exception as e:
            err_str = str(e).lower()
            is_auth_err = ("401" in err_str or "unauthorized" in err_str
                           or "authentication" in err_str
                           or ("invalid" in err_str and "api" in err_str))
            if is_auth_err:
                count = self._auth_failures.get(provider.name, 0) + 1
                self._auth_failures[provider.name] = count
                if count >= _CIRCUIT_BREAKER_THRESHOLD:
                    self._tripped_until[provider.name] = _time.time() + _CIRCUIT_BREAKER_COOLDOWN
                    logger.error(
                        "Circuit breaker TRIPPED for '%s' after %d consecutive auth failures. "
                        "Pausing for %ds. Fix your API key and reload config.",
                        provider.name, count, _CIRCUIT_BREAKER_COOLDOWN,
                    )
            raise

        # Successful call — reset circuit breaker for this provider
        self._auth_failures.pop(provider.name, None)
        self._tripped_until.pop(provider.name, None)

        self._total_calls += 1
        self._total_tokens += sum(response.usage.values())

        if self._event_bus:
            await self._event_bus.emit_dict("llm.response", {
                "task_type": task_type,
                "provider": response.provider,
                "model": response.model,
                "usage": response.usage,
                "content_preview": response.content[:200] if response.content else "",
                "elapsed_ms": round((_time.time() - _req_start) * 1000, 1),
            })

        return response

    def _resolve_function_routing(
        self,
        task_type: str,
        is_vision: bool,
        explicit_provider: str | None,
        explicit_model: str | None,
    ) -> tuple[str | None, str | None]:
        """Resolve provider name and model from function config + defaults.

        Returns (provider_name_or_None, model_or_None).
        """
        if explicit_provider and explicit_model:
            return explicit_provider, explicit_model

        func_cfg = self._functions.get(task_type) or self._functions.get(
            TASK_TYPE_ALIASES.get(task_type, ""), {}
        )
        if not func_cfg:
            func_cfg = {}

        if is_vision:
            prov = explicit_provider or func_cfg.get("vision_provider") or self._defaults.get("vision_provider")
            tier = func_cfg.get("vision_tier") or self._defaults.get("vision_tier") or "vision"
            mdl = explicit_model or func_cfg.get("vision_model")
        else:
            prov = explicit_provider or func_cfg.get("text_provider") or self._defaults.get("text_provider")
            tier = func_cfg.get("text_tier") or self._defaults.get("text_tier") or "fast"
            mdl = explicit_model or func_cfg.get("text_model")

        if mdl:
            return prov, mdl

        if prov:
            provider_obj = self._providers.get(prov)
            if provider_obj and provider_obj.available():
                resolved = provider_obj.list_models().get(tier)
                if resolved:
                    return prov, resolved

        return prov, None

    def _resolve_provider(self, name: str | None) -> LLMProvider | None:
        if name:
            p = self._providers.get(name)
            if p and p.available():
                return p

        p = self._providers.get(self._default_provider)
        if p and p.available():
            return p

        for p in self._providers.values():
            if p.available():
                return p
        return None

    def reset_circuit_breakers(self) -> None:
        """Reset all circuit breakers (call after config reload / key change)."""
        self._auth_failures.clear()
        self._tripped_until.clear()

    def status(self) -> dict:
        return {
            "providers": {
                name: {"available": p.available(), "models": p.list_models()}
                for name, p in self._providers.items()
            },
            "default_provider": self._default_provider,
            "routing": self._routing,
            "defaults": self._defaults,
            "functions": self._functions,
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
        defaults=llm_config.get("defaults"),
        functions=llm_config.get("functions"),
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
