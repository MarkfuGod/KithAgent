"""User-facing settings helpers shared by CLI, daemon, and desktop app."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

SCAN_CONFIG_PATH = Path.home() / ".agent_sys" / "scan_config.yaml"
LLM_CONFIG_PATH = Path.home() / ".agent_sys" / "llm_config.yaml"
DESKTOP_LLM_CONFIG_PATH = Path.home() / ".agent_sys" / "desktop_llm_config.yaml"


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


def normalize_openai_base_url(raw_host: str, raw_path: str = "") -> str:
    """Convert Chatbox-style host/path fields to an SDK base_url."""
    host = str(raw_host or "").strip().rstrip("/")
    path = str(raw_path or "").strip()
    if not host:
        return ""
    if path and not host.endswith(path.rstrip("/")) and path != "/chat/completions":
        host = f"{host}/{path.lstrip('/')}".rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/completions"):
        if host.endswith(suffix):
            host = host[: -len(suffix)]
            break
    return host.rstrip("/")


def _read_yaml_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            saved = yaml.safe_load(f) or {}
        return saved if isinstance(saved, dict) else {}
    except Exception:
        return {}


def _read_llm_config() -> dict[str, Any]:
    return _read_yaml_config(LLM_CONFIG_PATH)


def _read_desktop_llm_config() -> dict[str, Any]:
    return _read_yaml_config(DESKTOP_LLM_CONFIG_PATH)


def _first_model(provider_config: dict[str, Any], ui: dict[str, Any]) -> str:
    models = provider_config.get("models") if isinstance(provider_config, dict) else {}
    if not isinstance(models, dict):
        models = {}
    return str(
        ui.get("model")
        or models.get("fast")
        or models.get("strong")
        or models.get("vision")
        or ""
    )


def _is_header_safe_ascii(value: str) -> bool:
    return not any(ord(ch) > 255 or ch in "\r\n" for ch in value)


def _safe_scope_payload(
    scope_name: str,
    scope: dict[str, Any],
    env_vars: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(scope, dict) or not scope:
        return None

    provider_config = scope.get("provider_config")
    if not isinstance(provider_config, dict):
        provider_config = {}
    ui = scope.get("ui")
    if not isinstance(ui, dict):
        ui = {}

    provider = str(scope.get("provider") or "")
    api_key_env = str(
        scope.get("api_key_env")
        or provider_config.get("api_key_env")
        or ""
    )
    base_url = str(scope.get("base_url") or provider_config.get("base_url") or "")
    model = _first_model(provider_config, ui)
    capabilities = ui.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = {}

    key_value = str(env_vars.get(api_key_env) or "") if api_key_env else ""
    env_value = str(os.environ.get(api_key_env) or "") if api_key_env else ""
    has_key = bool(
        (key_value and _is_header_safe_ascii(key_value))
        or (env_value and _is_header_safe_ascii(env_value))
    )

    return {
        "scope": scope_name,
        "mode": str(scope.get("mode") or "api"),
        "provider": provider,
        "base_url": base_url,
        "api_key_env": api_key_env,
        "has_key": has_key,
        "ui": {
            "name": str(ui.get("name") or provider or ""),
            "api_mode": str(ui.get("api_mode") or provider or "openai_compatible"),
            "api_host": str(ui.get("api_host") or base_url),
            "api_path": str(ui.get("api_path") or "/chat/completions"),
            "model": model,
            "model_display_name": str(ui.get("model_display_name") or model),
            "model_type": str(ui.get("model_type") or "chat"),
            "capabilities": {
                "vision": bool(capabilities.get("vision")),
                "reasoning": bool(capabilities.get("reasoning")),
                "tools": bool(capabilities.get("tools")),
            },
            "context_window": str(ui.get("context_window") or ""),
            "max_output_tokens": str(ui.get("max_output_tokens") or ""),
            "improve_compatibility": bool(ui.get("improve_compatibility", True)),
        },
    }


def load_model_settings() -> dict[str, Any]:
    """Return saved model settings without exposing API key values."""
    backend_saved = _read_llm_config()
    desktop_saved = _read_desktop_llm_config()
    backend_env_vars = backend_saved.get("env_vars") if isinstance(backend_saved.get("env_vars"), dict) else {}
    desktop_env_vars = desktop_saved.get("env_vars") if isinstance(desktop_saved.get("env_vars"), dict) else {}
    backend_scopes = backend_saved.get("scopes") if isinstance(backend_saved.get("scopes"), dict) else {}
    desktop_scopes = desktop_saved.get("scopes") if isinstance(desktop_saved.get("scopes"), dict) else {}

    safe_scopes: dict[str, Any] = {}
    desktop_scope_payload = _safe_scope_payload("desktop", desktop_scopes.get("desktop") or {}, desktop_env_vars)
    if not desktop_scope_payload:
        # One-time compatibility with older builds that stored desktop settings
        # inside the backend llm_config.yaml.
        desktop_scope_payload = _safe_scope_payload("desktop", backend_scopes.get("desktop") or {}, backend_env_vars)
    if desktop_scope_payload:
        safe_scopes["desktop"] = desktop_scope_payload

    backend_scope_payload = _safe_scope_payload("backend", backend_scopes.get("backend") or {}, backend_env_vars)
    if backend_scope_payload:
        safe_scopes["backend"] = backend_scope_payload

    if "backend" not in safe_scopes:
        providers = backend_saved.get("providers") if isinstance(backend_saved.get("providers"), dict) else {}
        default_provider = str(backend_saved.get("default_provider") or "")
        provider_config = providers.get(default_provider) if default_provider else {}
        if isinstance(provider_config, dict) and provider_config:
            legacy_backend = {
                "mode": str(backend_saved.get("mode") or "api"),
                "provider": default_provider,
                "base_url": provider_config.get("base_url") or "",
                "api_key_env": provider_config.get("api_key_env") or "",
                "provider_config": provider_config,
                "ui": backend_saved.get("ui") if isinstance(backend_saved.get("ui"), dict) else {},
            }
            scope_payload = _safe_scope_payload("backend", legacy_backend, backend_env_vars)
            if scope_payload:
                safe_scopes["backend"] = scope_payload

    return {
        "exists": bool(backend_saved or desktop_saved),
        "mode": str(backend_saved.get("mode") or ""),
        "desktop_mode": str(desktop_saved.get("desktop_mode") or ""),
        "default_provider": str(backend_saved.get("default_provider") or ""),
        "scopes": safe_scopes,
    }


def load_desktop_runtime_model_settings() -> dict[str, Any]:
    """Return desktop LLM runtime settings, including key, for local Electron main only."""
    saved = _read_desktop_llm_config()
    if not saved:
        saved = _read_llm_config()
    env_vars = saved.get("env_vars") if isinstance(saved.get("env_vars"), dict) else {}
    scopes = saved.get("scopes") if isinstance(saved.get("scopes"), dict) else {}
    scope = scopes.get("desktop") if isinstance(scopes.get("desktop"), dict) else {}
    if not scope:
        return {}

    provider_config = scope.get("provider_config") if isinstance(scope.get("provider_config"), dict) else {}
    ui = scope.get("ui") if isinstance(scope.get("ui"), dict) else {}
    api_key_env = str(scope.get("api_key_env") or provider_config.get("api_key_env") or "")
    api_path = str(ui.get("api_path") or "/chat/completions")
    api_host = str(ui.get("api_host") or scope.get("base_url") or provider_config.get("base_url") or "")
    model = _first_model(provider_config, ui)
    key_value = str(env_vars.get(api_key_env) or "") if api_key_env else ""
    env_value = str(os.environ.get(api_key_env) or "") if api_key_env else ""
    api_key = key_value if key_value and _is_header_safe_ascii(key_value) else ""
    if not api_key and env_value and _is_header_safe_ascii(env_value):
        api_key = env_value
    return {
        "mode": str(scope.get("mode") or saved.get("desktop_mode") or "api"),
        "provider": str(scope.get("provider") or ui.get("api_mode") or "openai_compatible"),
        "api_mode": str(ui.get("api_mode") or scope.get("provider") or "openai_compatible"),
        "base_url": normalize_openai_base_url(api_host, api_path),
        "api_host": api_host,
        "api_path": api_path,
        "api_key": api_key,
        "api_key_env": api_key_env,
        "model": model,
        "model_display_name": str(ui.get("model_display_name") or model),
        "model_type": str(ui.get("model_type") or "chat"),
        "max_output_tokens": str(ui.get("max_output_tokens") or ""),
    }


def save_model_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Persist a plain-language model choice for the desktop app.

    mode=ollama writes an OpenAI-compatible provider pointed at localhost.
    mode=api writes a user-selected OpenAI-compatible or official provider.
    mode=local disables remote providers for rule-based/local fallbacks.
    """
    mode = str(settings.get("mode") or "api")
    scope = str(settings.get("scope") or "backend")
    model = str(settings.get("model") or "qwen3.6-plus")
    provider = str(settings.get("provider") or settings.get("api_mode") or "openai_compatible")
    api_host = str(settings.get("api_host") or settings.get("base_url") or "")
    api_path = str(settings.get("api_path") or "/chat/completions")
    base_url = normalize_openai_base_url(api_host, api_path)
    api_key = str(settings.get("api_key") or "")
    if api_key and not _is_header_safe_ascii(api_key):
        raise ValueError("API Key 只能包含 HTTP header 可用的 ASCII 字符；请不要把中文说明或模型回复填进 API Key。")
    api_key_env_override = str(settings.get("api_key_env") or "").strip()
    env_by_provider = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
        "anthropic_compatible": "ANTHROPIC_COMPATIBLE_API_KEY",
    }
    normalized_provider = provider if provider in env_by_provider else "openai_compatible"
    api_key_env = api_key_env_override or env_by_provider[normalized_provider]

    existing: dict[str, Any] = _read_desktop_llm_config() if scope == "desktop" else _read_llm_config()

    payload: dict[str, Any] = existing if isinstance(existing, dict) else {}
    payload.update({
        "mode": payload.get("mode") or mode,
        "providers": payload.get("providers") or {},
        "env_vars": payload.get("env_vars") or {},
    })
    scope_ui = {
            "name": str(settings.get("name") or ""),
            "api_mode": str(settings.get("api_mode") or provider),
            "api_host": api_host,
            "api_path": api_path,
            "model": model,
            "model_display_name": str(settings.get("model_display_name") or model),
            "model_type": str(settings.get("model_type") or "chat"),
            "capabilities": {
                "vision": bool(settings.get("vision")),
                "reasoning": bool(settings.get("reasoning")),
                "tools": bool(settings.get("tools")),
            },
            "context_window": str(settings.get("context_window") or ""),
            "max_output_tokens": str(settings.get("max_output_tokens") or ""),
            "improve_compatibility": bool(settings.get("improve_compatibility", True)),
    }
    payload["scopes"] = payload.get("scopes") or {}
    provider_cfg: dict[str, Any] = {
        "api_key_env": api_key_env,
        "models": {"fast": model, "strong": model, "vision": model},
    }
    if normalized_provider.endswith("compatible"):
        provider_cfg["base_url"] = base_url
    if mode == "ollama":
        provider_cfg["base_url"] = base_url

    payload["scopes"][scope] = {
        "mode": mode,
        "provider": normalized_provider,
        "base_url": base_url,
        "api_key_env": api_key_env,
        "provider_config": provider_cfg,
        "ui": scope_ui,
    }
    payload["ui"] = scope_ui

    if scope == "desktop":
        payload["desktop_mode"] = mode
        if mode == "ollama":
            payload["env_vars"][api_key_env] = api_key or "ollama"
        elif mode != "local" and api_key:
            payload["env_vars"][api_key_env] = api_key
        for key, value in payload.get("env_vars", {}).items():
            if value:
                os.environ[key] = value
        DESKTOP_LLM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(DESKTOP_LLM_CONFIG_PATH, "w") as f:
            yaml.dump(payload, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        os.chmod(str(DESKTOP_LLM_CONFIG_PATH), 0o600)
        return {"saved": True, "mode": mode, "provider": normalized_provider, "scope": scope}

    payload["providers"] = {}
    payload["env_vars"] = {}
    payload["mode"] = mode
    if isinstance(payload.get("scopes"), dict):
        payload["scopes"].pop("desktop", None)

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
        payload["default_provider"] = normalized_provider
        backend_provider_cfg: dict[str, Any] = {
            "api_key_env": api_key_env,
            "models": {"fast": model, "strong": model, "vision": model},
        }
        if normalized_provider.endswith("compatible"):
            backend_provider_cfg["base_url"] = base_url
        payload["providers"][normalized_provider] = backend_provider_cfg
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
