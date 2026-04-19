"""
Web Dashboard — visual debugging console for AgentOS.

This module is intentionally slim: it only composes the aiohttp app,
serves the SPA shell + static assets, and prints the startup banner.
All API handlers live in `src/web/api/<area>.py` grouped by feature
(overview, search, triage, knowledge, llm_config, scheduling, daemon,
events) so the dashboard's 20+ endpoints aren't stacked into one giant
file anymore.

The dashboard reads `~/.agent_sys/memory.db` directly, so it works
without a running daemon for everything except live status, event
streaming, and agent triggering — those proxy to the daemon at
127.0.0.1:7437.

Usage:
    agent-sys dashboard            # start on default port 7438
    agent-sys dashboard --port 9000
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from src.web._utils import DB_PATH
from src.web.api import (
    daemon as daemon_api,
    events as events_api,
    knowledge as knowledge_api,
    llm_config as llm_api,
    overview as overview_api,
    scheduling as scheduling_api,
    search as search_api,
    triage as triage_api,
)

logger = logging.getLogger("agent_sys.dashboard")

_STATIC_DIR = Path(__file__).parent / "static"
_SHELL_HTML = Path(__file__).parent / "dashboard.html"


# ── Static shell + assets ─────────────────────────────────────────

async def serve_dashboard(request: web.Request) -> web.Response:
    return web.Response(text=_SHELL_HTML.read_text(), content_type="text/html")


_CONTENT_TYPES = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".map": "application/json",
}


async def serve_static(request: web.Request) -> web.Response:
    """Serve CSS/JS/image assets from src/web/static/, with path-traversal guard."""
    filename = request.match_info["filename"]
    safe_path = (_STATIC_DIR / filename).resolve()
    if not str(safe_path).startswith(str(_STATIC_DIR.resolve())):
        return web.Response(status=403)
    if not safe_path.exists() or not safe_path.is_file():
        return web.Response(status=404)
    ct = _CONTENT_TYPES.get(safe_path.suffix, "application/octet-stream")
    return web.Response(body=safe_path.read_bytes(), content_type=ct)


# ── App composition ───────────────────────────────────────────────

def create_app(event_bus=None) -> web.Application:
    """Build the aiohttp app. `event_bus` is optional — when the dashboard
    is started in-process by the kernel it'll be passed in so SSE can
    stream events from the local bus instead of proxying to the daemon."""
    app = web.Application()
    if event_bus is not None:
        app["event_bus"] = event_bus

    app.router.add_get("/", serve_dashboard)
    app.router.add_get("/static/{filename}", serve_static)

    # Overview / files / directories / recent / summary-progress
    app.router.add_get("/api/overview", overview_api.overview)
    app.router.add_get("/api/directories", overview_api.directory_tree)
    app.router.add_get("/api/recent", overview_api.recent_files)
    app.router.add_get("/api/summary-progress", overview_api.summary_progress)

    # Search
    app.router.add_get("/api/search", search_api.file_search)

    # Triage — old shape stays for back-compat, new 'skipped' endpoint for v0.7
    app.router.add_get("/api/triage", triage_api.triage)
    app.router.add_get("/api/triage/skipped-directories", triage_api.skipped_directories)

    # Knowledge + scheduling history
    app.router.add_get("/api/knowledge", knowledge_api.knowledge)
    app.router.add_get("/api/scheduling", knowledge_api.scheduling_history)

    # LLM + embedding config
    app.router.add_get("/api/llm-config", llm_api.llm_config_get)
    app.router.add_post("/api/llm-config", llm_api.llm_config_save)
    app.router.add_get("/api/llm-routing", llm_api.llm_routing_get)
    app.router.add_post("/api/llm-routing", llm_api.llm_routing_save)
    app.router.add_get("/api/embedding-info", llm_api.embedding_info)
    app.router.add_get("/api/embedding-config", llm_api.embedding_config_get)
    app.router.add_post("/api/embedding-config", llm_api.embedding_config_save)

    # Cron strategy
    app.router.add_get("/api/scheduling-strategy", scheduling_api.strategy_get)
    app.router.add_post("/api/scheduling-strategy", scheduling_api.strategy_set)

    # Daemon proxies
    app.router.add_get("/api/daemon", daemon_api.status)
    app.router.add_post("/api/trigger-agent", daemon_api.trigger_agent)
    app.router.add_post("/api/reload-config", daemon_api.reload_config)

    # SSE
    app.router.add_get("/api/events", events_api.events_sse)

    return app


def run_dashboard(port: int = 7438) -> None:
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        print("Start the daemon first with: agent-sys start")
        return

    app = create_app()
    print(f"""
╔══════════════════════════════════════════════════╗
║         AgentOS — Dashboard                      ║
║                                                  ║
║   URL:  http://127.0.0.1:{port:<5}                  ║
║   DB:   {str(DB_PATH):<40} ║
║                                                  ║
║   Open in your browser to explore.               ║
║   Press Ctrl+C to stop.                          ║
╚══════════════════════════════════════════════════╝
    """)
    web.run_app(app, host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--port", "-p", type=int, default=7438)
    args = p.parse_args()
    run_dashboard(args.port)
