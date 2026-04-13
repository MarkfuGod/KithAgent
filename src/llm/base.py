"""
LLM Provider Base — unified interface for all model backends.

Every provider (OpenAI, Claude, OpenAI-compatible) implements this
abstract interface so the rest of AgentOS never deals with
provider-specific details.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent_sys.llm")


@dataclass
class LLMMessage:
    role: str          # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    usage: dict[str, int] = field(default_factory=dict)  # prompt_tokens, completion_tokens
    raw: Any = None


class LLMProvider(ABC):
    """Abstract base for all LLM providers."""

    name: str = "base"

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        **kwargs,
    ) -> LLMResponse:
        ...

    @abstractmethod
    def available(self) -> bool:
        """Whether this provider is configured and usable."""
        ...

    @abstractmethod
    def list_models(self) -> dict[str, str]:
        """Return {tier: model_name} mapping, e.g. {'fast': 'gpt-4o-mini', 'strong': 'gpt-4o'}."""
        ...
