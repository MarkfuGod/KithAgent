"""
OpenAI-Compatible Adapter — supports any provider that implements the
OpenAI chat completions API format.

Works with: DeepSeek, Groq, Together, Ollama, vLLM, LiteLLM, etc.
"""

from __future__ import annotations

import logging
import os

from src.llm.base import LLMMessage, LLMProvider, LLMResponse
from src.llm.openai_adapter import OpenAIAdapter

logger = logging.getLogger("agent_sys.llm.openai_compatible")


class OpenAICompatibleAdapter(OpenAIAdapter):
    """Thin wrapper over OpenAIAdapter with a different base_url and name."""

    name = "openai_compatible"
    _PLACEHOLDER_KEY = "sk-placeholder"

    def __init__(
        self,
        base_url: str = "",
        api_key: str | None = None,
        api_key_env: str = "OPENAI_COMPATIBLE_API_KEY",
        models: dict[str, str] | None = None,
    ):
        self._explicit_key = api_key or os.environ.get(api_key_env, "")
        self._explicit_url = base_url or os.environ.get("OPENAI_COMPATIBLE_BASE_URL", "")
        resolved_models = models or {"fast": "default", "strong": "default"}

        if not self._explicit_url:
            logger.warning(
                "openai_compatible: no base_url configured. "
                "Set base_url in llm_config.yaml or OPENAI_COMPATIBLE_BASE_URL env var."
            )

        super().__init__(
            api_key=self._explicit_key or self._PLACEHOLDER_KEY,
            base_url=self._explicit_url or "https://localhost:0/v1",
            models=resolved_models,
        )

    def available(self) -> bool:
        has_real_key = bool(self._explicit_key) and self._explicit_key != self._PLACEHOLDER_KEY
        has_explicit_url = bool(self._explicit_url)
        return has_real_key and has_explicit_url


# Backward compat alias
CompatibleAdapter = OpenAICompatibleAdapter
