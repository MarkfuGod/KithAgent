"""
Cron / scheduling-strategy selector endpoints.

Reads and writes the `cron.adaptive.strategy` field in `config/default.yaml`,
which the running daemon picks up via hot-reload (or restart).
"""

from __future__ import annotations

import yaml
from aiohttp import web

from src.web._utils import DEFAULT_CONFIG_PATH


async def strategy_get(request: web.Request) -> web.Response:
    from src.kernel.cron import SCHEDULING_STRATEGIES

    current = "balanced"
    try:
        if DEFAULT_CONFIG_PATH.exists():
            with open(DEFAULT_CONFIG_PATH) as f:
                raw = yaml.safe_load(f) or {}
            current = raw.get("cron", {}).get("adaptive", {}).get("strategy", "balanced")
    except Exception:
        pass

    strategies = []
    for name, s in SCHEDULING_STRATEGIES.items():
        entry = {"name": name, "description": s.get("description", "")}
        entry["policies"] = {k: v for k, v in s.items() if k != "description"}
        strategies.append(entry)

    return web.json_response({"current": current, "strategies": strategies})


async def strategy_set(request: web.Request) -> web.Response:
    from src.kernel.cron import SCHEDULING_STRATEGIES

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    name = body.get("strategy", "")
    if name not in SCHEDULING_STRATEGIES:
        return web.json_response({"error": f"Unknown strategy: {name}"}, status=400)

    try:
        with open(DEFAULT_CONFIG_PATH) as f:
            raw = yaml.safe_load(f) or {}

        raw.setdefault("cron", {}).setdefault("adaptive", {})["strategy"] = name

        with open(DEFAULT_CONFIG_PATH, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        return web.json_response({
            "success": True, "strategy": name,
            "message": "Strategy updated. Restart daemon to apply.",
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
