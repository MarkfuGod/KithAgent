"""
LLM + embedding configuration endpoints.

Two independent config surfaces:
  - `~/.agent_sys/llm_config.yaml` — per-user LLM provider keys/models.
    Edited via `/api/llm-config` GET/POST. Written with 0600 perms.
  - `config/default.yaml`          — system-wide defaults. Edited via
    `/api/llm-routing` (per-function routing) and `/api/embedding-config`.

Kept together because the dashboard's Settings tab treats them as one
page, even though they live in different files on disk.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from aiohttp import web

from src.web._utils import DEFAULT_CONFIG_PATH, LLM_CONFIG_PATH


# ── LLM provider config (~/.agent_sys/llm_config.yaml) ────────────

async def llm_config_get(request: web.Request) -> web.Response:
    if not LLM_CONFIG_PATH.exists():
        return web.json_response({"exists": False, "config": {}})

    try:
        with open(LLM_CONFIG_PATH) as f:
            config = yaml.safe_load(f) or {}

        safe_config = {
            "default_provider": config.get("default_provider", ""),
            "providers": {},
        }
        env_vars = config.get("env_vars", {}) or {}
        for name, pcfg in config.get("providers", {}).items():
            key_env = pcfg.get("api_key_env", "")
            safe_config["providers"][name] = {
                "base_url": pcfg.get("base_url", ""),
                "api_key_env": key_env,
                "models": pcfg.get("models", {}),
                "has_key": bool(env_vars.get(key_env)),
            }
        return web.json_response({"exists": True, "config": safe_config})
    except Exception as e:
        return web.json_response({"exists": False, "error": str(e)})


async def llm_config_save(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    try:
        existing = {}
        if LLM_CONFIG_PATH.exists():
            with open(LLM_CONFIG_PATH) as f:
                existing = yaml.safe_load(f) or {}

        if "default_provider" in body:
            existing["default_provider"] = body["default_provider"]

        if "providers" in body:
            existing.setdefault("providers", {})
            for name, pcfg in body["providers"].items():
                existing["providers"].setdefault(name, {})
                p = existing["providers"][name]
                if "base_url" in pcfg:
                    p["base_url"] = pcfg["base_url"]
                if "api_key_env" in pcfg:
                    p["api_key_env"] = pcfg["api_key_env"]
                if "models" in pcfg:
                    p["models"] = pcfg["models"]

        if "env_vars" in body:
            existing.setdefault("env_vars", {})
            for k, v in body["env_vars"].items():
                if v:
                    existing["env_vars"][k] = v

        if "delete_provider" in body:
            pname = body["delete_provider"]
            existing.get("providers", {}).pop(pname, None)

        LLM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LLM_CONFIG_PATH, "w") as f:
            yaml.dump(existing, f, default_flow_style=False)
        os.chmod(str(LLM_CONFIG_PATH), 0o600)

        return web.json_response({
            "success": True,
            "message": "Config saved. Restart daemon to apply.",
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ── Per-function routing (config/default.yaml: llm.functions) ─────

async def llm_routing_get(request: web.Request) -> web.Response:
    try:
        with open(DEFAULT_CONFIG_PATH) as f:
            raw = yaml.safe_load(f) or {}
        llm = raw.get("llm", {})
        providers = {}
        for pname, pcfg in llm.get("providers", {}).items():
            tiers = list((pcfg.get("models") or {}).keys())
            providers[pname] = {"tiers": tiers, "models": pcfg.get("models", {})}
        return web.json_response({
            "default_provider": llm.get("default_provider", ""),
            "providers": providers,
            "defaults": llm.get("defaults", {}),
            "functions": llm.get("functions", {}),
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def llm_routing_save(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    try:
        with open(DEFAULT_CONFIG_PATH) as f:
            raw = yaml.safe_load(f) or {}

        llm = raw.setdefault("llm", {})
        if "defaults" in body:
            llm["defaults"] = body["defaults"]
        if "functions" in body:
            llm["functions"] = body["functions"]

        with open(DEFAULT_CONFIG_PATH, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        return web.json_response({
            "success": True,
            "message": "Routing updated. Restart daemon to apply.",
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ── Embeddings ────────────────────────────────────────────────────

async def embedding_info(request: web.Request) -> web.Response:
    """Live embedding provider info (reflects current process state)."""
    from src.memory import embeddings
    return web.json_response(embeddings.get_provider_info())


async def embedding_config_get(request: web.Request) -> web.Response:
    from src.memory import embeddings
    try:
        with open(DEFAULT_CONFIG_PATH) as f:
            raw = yaml.safe_load(f) or {}
        emb_cfg = raw.get("memory", {}).get("embedding", {})
        live_info = embeddings.get_provider_info()
        return web.json_response({"config": emb_cfg, "live": live_info})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def embedding_config_save(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    try:
        with open(DEFAULT_CONFIG_PATH) as f:
            raw = yaml.safe_load(f) or {}

        raw.setdefault("memory", {})["embedding"] = body.get("embedding", {})

        with open(DEFAULT_CONFIG_PATH, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        return web.json_response({
            "success": True,
            "message": "Embedding config updated. Restart daemon to apply.",
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
