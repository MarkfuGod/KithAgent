"""User-facing settings helpers shared by CLI, daemon, and desktop app."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

SCAN_CONFIG_PATH = Path.home() / ".agent_sys" / "scan_config.yaml"
LLM_CONFIG_PATH = Path.home() / ".agent_sys" / "llm_config.yaml"


def normalize_watch_paths(paths: list[str]) -> list[str]:
    """Expand and normalize user-selected source directories."""
    normalized: list[str] = []
    for raw in paths:
        if not raw:
            continue
        path = Path(os.path.expanduser(str(raw))).expanduser().resolve()
        normalized.append(str(path))
    return list(dict.fromkeys(normalized))


def load_scan_settings(default_paths: list[str] | None = None) -> dict[str, Any]:
    paths = default_paths or []
    if SCAN_CONFIG_PATH.exists():
        try:
            with open(SCAN_CONFIG_PATH) as f:
                saved = yaml.safe_load(f) or {}
            paths = saved.get("watch_paths") or paths
        except Exception:
            pass
    return {"watch_paths": normalize_watch_paths([str(p) for p in paths])}


def save_scan_settings(watch_paths: list[str]) -> dict[str, Any]:
    normalized = normalize_watch_paths(watch_paths)
    SCAN_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SCAN_CONFIG_PATH, "w") as f:
        yaml.dump({"watch_paths": normalized}, f, default_flow_style=False, allow_unicode=True)
    os.chmod(str(SCAN_CONFIG_PATH), 0o600)
    return {"watch_paths": normalized}


def save_model_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Persist a plain-language model choice for the desktop app.

    mode=ollama writes an OpenAI-compatible provider pointed at localhost.
    mode=api writes a user-selected OpenAI-compatible or official provider.
    mode=local disables remote providers for rule-based/local fallbacks.
    """
    mode = str(settings.get("mode") or "api")
    model = str(settings.get("model") or "qwen3.6-plus")
    base_url = str(settings.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1")
    provider = str(settings.get("provider") or "openai_compatible")
    api_key = str(settings.get("api_key") or "")
    api_key_env_override = str(settings.get("api_key_env") or "").strip()

    payload: dict[str, Any] = {"mode": mode, "providers": {}, "env_vars": {}}

    if mode == "local":
        payload["default_provider"] = ""
    elif mode == "ollama":
        payload["default_provider"] = "openai_compatible"
        payload["providers"]["openai_compatible"] = {
            "base_url": base_url,
            "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
            "models": {"fast": model, "strong": model, "vision": model},
            "extra_body": {"think": False},
        }
        payload["env_vars"]["OPENAI_COMPATIBLE_API_KEY"] = api_key or "ollama"
    else:
        provider = provider if provider in {"openai", "anthropic", "openai_compatible", "anthropic_compatible"} else "openai_compatible"
        env_by_provider = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
            "anthropic_compatible": "ANTHROPIC_COMPATIBLE_API_KEY",
        }
        api_key_env = api_key_env_override or env_by_provider[provider]
        payload["default_provider"] = provider
        provider_cfg: dict[str, Any] = {
            "api_key_env": api_key_env,
            "models": {"fast": model, "strong": model, "vision": model},
        }
        if provider.endswith("compatible"):
            provider_cfg["base_url"] = base_url
        payload["providers"][provider] = provider_cfg
        if api_key:
            payload["env_vars"][api_key_env] = api_key

    LLM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LLM_CONFIG_PATH, "w") as f:
        yaml.dump(payload, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    os.chmod(str(LLM_CONFIG_PATH), 0o600)

    for key, value in payload.get("env_vars", {}).items():
        if value:
            os.environ[key] = value
    return {"saved": True, "mode": mode, "provider": payload.get("default_provider", "")}
