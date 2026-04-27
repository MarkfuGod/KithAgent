"""
Shared helpers for dashboard API handlers.

Everything that touches the SQLite DB or the on-disk auth token goes through
here so the per-feature api submodules stay small and consistent.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from aiohttp import web

DB_PATH = Path.home() / ".agent_sys" / "memory.db"
LLM_CONFIG_PATH = Path.home() / ".agent_sys" / "llm_config.yaml"
AUTH_TOKEN_PATH = Path.home() / ".agent_sys" / "auth_token"
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "default.yaml"
DASHBOARD_MUTATION_HEADER = "X-Kith-Dashboard"

# Daemon HTTP port — the syscall/events server the dashboard proxies to.
DAEMON_HTTP_PORT = 7437


def get_db() -> sqlite3.Connection:
    """Open a short-lived SQLite connection to the main memory DB."""
    db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    return db


def safe_json(raw: str) -> Any:
    """Best-effort JSON decode; falls back to the raw string on failure."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def auth_headers() -> dict[str, str]:
    """X-Agent-Token header for syscalls made against the daemon."""
    try:
        if AUTH_TOKEN_PATH.exists():
            token = AUTH_TOKEN_PATH.read_text().strip()
            if token:
                return {"X-Agent-Token": token}
    except Exception:
        pass
    return {}


@web.middleware
async def require_dashboard_mutation_header(request, handler):
    """Require a non-simple header for local dashboard mutations.

    The dashboard is bound to localhost, but browsers can still submit simple
    cross-site POSTs to localhost. Requiring this custom header blocks those
    requests because they need a CORS preflight that this app does not allow.
    """
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.headers.get(DASHBOARD_MUTATION_HEADER) != "1":
            return web.json_response(
                {"error": "dashboard mutation header required"},
                status=403,
            )
    return await handler(request)
