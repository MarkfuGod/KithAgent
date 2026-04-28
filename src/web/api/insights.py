"""Personal insights endpoint shared with the desktop app."""

from __future__ import annotations

from aiohttp import web

from src.insights import build_insights
from src.web._utils import get_db


async def insights(request: web.Request) -> web.Response:
    limit = int(request.query.get("limit", "12"))
    db = get_db()
    try:
        return web.json_response(build_insights(db, limit=limit))
    finally:
        db.close()
