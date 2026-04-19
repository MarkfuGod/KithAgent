"""
Endpoints that delegate to the running daemon via its HTTP syscall surface.

The dashboard is otherwise self-sufficient (reads SQLite directly), but
a few things — triggering agents, hot-reloading config, checking live
subsystem status — only make sense against the live daemon.
"""

from __future__ import annotations

from aiohttp import web

from src.web._utils import DAEMON_HTTP_PORT, auth_headers


async def status(request: web.Request) -> web.Response:
    """Live kernel status, with a graceful 'not connected' fallback."""
    try:
        import aiohttp as ah
        async with ah.ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{DAEMON_HTTP_PORT}/status",
                timeout=ah.ClientTimeout(total=3),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return web.json_response({"connected": True, **data})
    except Exception:
        pass
    return web.json_response({"connected": False})


_ALLOWED_AGENTS = frozenset({
    "triage", "summarizer", "behavior_analyzer",
    "priority_classifier", "report_generator", "profile_builder",
})


async def trigger_agent(request: web.Request) -> web.Response:
    """POST {agent, input_data} → submit as an agent task via syscall."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    agent_name = body.get("agent", "")
    input_data = body.get("input_data", {})

    if not agent_name:
        return web.json_response({"error": "Missing 'agent' parameter"}, status=400)
    if agent_name not in _ALLOWED_AGENTS:
        return web.json_response(
            {"error": f"Agent '{agent_name}' not in allowed list"},
            status=400,
        )

    syscall_payload = {
        "call_type": "agent.submit",
        "params": {"agent_name": agent_name, "input_data": input_data},
        "caller": "dashboard",
    }

    try:
        import aiohttp as ah
        async with ah.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{DAEMON_HTTP_PORT}/syscall",
                json=syscall_payload,
                headers=auth_headers(),
                timeout=ah.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if data.get("success"):
                    return web.json_response({
                        "success": True,
                        "task_id": data.get("data", {}).get("task_id"),
                        "message": f"Agent '{agent_name}' submitted successfully",
                    })
                return web.json_response({
                    "success": False,
                    "error": data.get("error", "Unknown error from daemon"),
                })
    except Exception as e:
        return web.json_response({
            "success": False,
            "error": f"Cannot reach daemon: {e}. Is it running?",
        }, status=503)


async def reload_config(request: web.Request) -> web.Response:
    try:
        import aiohttp as ah
        async with ah.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{DAEMON_HTTP_PORT}/reload",
                headers=auth_headers(),
                timeout=ah.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if data.get("reloaded"):
                    return web.json_response({
                        "success": True,
                        "message": "Config reloaded successfully",
                        **data,
                    })
                return web.json_response({
                    "success": False,
                    "error": data.get("error", "Unknown"),
                })
    except Exception as e:
        return web.json_response({
            "success": False,
            "error": f"Cannot reach daemon: {e}. Is it running?",
        }, status=503)
