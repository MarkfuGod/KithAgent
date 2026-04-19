"""
SSE endpoint — streams real-time EventBus events to the dashboard.

Two modes depending on how the dashboard is running:
  1. In-process (kernel started the dashboard and passed its event_bus in
     app["event_bus"]) — subscribe directly to the local bus.
  2. Standalone (`agent-sys dashboard` with daemon elsewhere) — open a
     long-polled SSE connection to the daemon's HTTP events endpoint and
     forward chunks verbatim.
"""

from __future__ import annotations

import asyncio
import json

from aiohttp import web

from src.web._utils import DAEMON_HTTP_PORT


async def events_sse(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await resp.prepare(request)

    event_bus = request.app.get("event_bus")
    if event_bus:
        await _stream_from_local_bus(event_bus, resp)
        return resp

    await _proxy_daemon_sse(resp)
    return resp


async def _stream_from_local_bus(event_bus, resp: web.StreamResponse) -> None:
    queue = event_bus.subscribe(replay_buffer=False)
    try:
        for evt in event_bus.recent_events(limit=30):
            line = json.dumps(evt, default=str)
            await resp.write(f"event: {evt['type']}\ndata: {line}\n\n".encode())

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30)
                await resp.write(event.to_sse().encode())
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
            except ConnectionResetError:
                break
    finally:
        event_bus.unsubscribe(queue)


async def _proxy_daemon_sse(resp: web.StreamResponse) -> None:
    try:
        import aiohttp as ah
        async with ah.ClientSession() as session:
            daemon_url = f"http://127.0.0.1:{DAEMON_HTTP_PORT}/events"
            async with session.get(
                daemon_url,
                timeout=ah.ClientTimeout(total=0, sock_read=0),
            ) as upstream:
                if upstream.status != 200:
                    await resp.write(
                        b"event: error\ndata: {\"msg\":\"Daemon not reachable for SSE proxy\"}\n\n"
                    )
                    return

                await resp.write(
                    b"event: info\ndata: {\"msg\":\"Connected to daemon SSE (proxy mode)\"}\n\n"
                )
                async for chunk in upstream.content.iter_any():
                    try:
                        await resp.write(chunk)
                    except ConnectionResetError:
                        break
    except Exception as e:
        msg = json.dumps({"msg": f"SSE proxy failed: {e}"})
        await resp.write(f"event: error\ndata: {msg}\n\n".encode())
