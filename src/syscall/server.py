"""
Syscall Server — the 'system call handler' of AgentOS.

Listens on a Unix socket (or HTTP) for requests from external agents.
Each request is a 'syscall' that gets dispatched to the appropriate
agent thread via the Scheduler.

This is how Cursor, Claude Code, or any other agent talks to SysAgent.

Protocol:
  Request:  length-prefixed JSON  (4 bytes big-endian + JSON payload)
  Response: length-prefixed JSON  (4 bytes big-endian + JSON payload)
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import struct
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.agents.base import AgentTask
from src.kernel.config import SyscallConfig
from src.syscall.protocol import (
    SYSCALL_TO_AGENT,
    SyscallRequest,
    SyscallResponse,
    SyscallType,
)

# Syscalls that are always safe to call without auth — they're used by
# the setup wizard / health probe and leak no privileged information.
_PUBLIC_SYSCALLS = frozenset({SyscallType.SYS_PING, SyscallType.SYS_STATUS})

if TYPE_CHECKING:
    from src.kernel.daemon import SysAgentKernel

logger = logging.getLogger("agent_sys.syscall")


class SyscallServer:
    """Listens for syscall requests and dispatches them to the kernel."""

    def __init__(self, config: SyscallConfig, kernel: SysAgentKernel):
        self.config = config
        self.kernel = kernel
        self._unix_server: asyncio.Server | None = None
        self._http_runner = None
        self._auth_token: str | None = None

    async def start(self) -> None:
        actual_socket = str(self.kernel.config.kernel.socket_path)

        if os.path.exists(actual_socket):
            os.unlink(actual_socket)

        self._auth_token = self._load_or_create_token()

        self._unix_server = await asyncio.start_unix_server(
            self._handle_connection, path=actual_socket,
        )
        os.chmod(actual_socket, 0o600)
        logger.info("Syscall server listening on %s", actual_socket)

        if self.config.transport == "http" or self.config.http_port:
            asyncio.create_task(self._start_http())

    # ── Auth helpers ──────────────────────────────────────────

    def _load_or_create_token(self) -> str | None:
        """Load the auth token from disk, or generate and persist one
        on first boot. Returns None if the path is unset."""
        token_path = Path(os.path.expanduser(str(self.config.auth_token_path)))
        try:
            token_path.parent.mkdir(parents=True, exist_ok=True)
            if token_path.exists():
                token = token_path.read_text().strip()
                if token:
                    return token
            token = secrets.token_urlsafe(32)
            token_path.write_text(token)
            os.chmod(token_path, 0o600)
            logger.info("Generated new syscall auth token at %s (chmod 0600)", token_path)
            return token
        except Exception as e:
            logger.warning("Failed to initialize auth token at %s: %s", token_path, e)
            return None

    def _check_auth(
        self,
        request: SyscallRequest,
        *,
        transport: str,
        http_token: str | None = None,
    ) -> str | None:
        """Return an error string if auth fails, or None if OK."""
        call_type = request.call_type
        if call_type in _PUBLIC_SYSCALLS:
            return None

        caller = (request.caller or "unknown").lower()
        allowed = {c.lower() for c in self.config.allowed_callers}
        # Empty allowed_callers = allow anyone (back-compat).
        if allowed and caller not in allowed:
            logger.warning(
                "Syscall auth: caller=%r not in allowed_callers (transport=%s)",
                caller, transport,
            )
            return f"caller '{request.caller}' not allowed"

        if transport == "http":
            if self.config.require_auth:
                if not self._auth_token:
                    return "auth token not configured on server"
                if not http_token or not hmac.compare_digest(http_token, self._auth_token):
                    logger.warning("Syscall auth: bad/missing HTTP token for caller=%r", caller)
                    return "invalid or missing X-Agent-Token"
            return None

        # Unix socket: local process with mode-600 socket. Trust by default
        # unless the operator explicitly opts out of anonymous unix calls.
        if not self.config.allow_unix_anonymous:
            if self.config.require_auth:
                sock_token = request.params.get("_auth_token") if isinstance(request.params, dict) else None
                if not self._auth_token or not sock_token or \
                   not hmac.compare_digest(str(sock_token), self._auth_token):
                    return "unix socket auth required"
        return None

    async def stop(self) -> None:
        if self._unix_server:
            self._unix_server.close()
            await self._unix_server.wait_closed()
            self._unix_server = None

        socket_path = str(self.kernel.config.kernel.socket_path)
        if os.path.exists(socket_path):
            os.unlink(socket_path)

        if self._http_runner:
            try:
                await self._http_runner.cleanup()
            except Exception:
                pass
            self._http_runner = None

        logger.info("Syscall server stopped")

    # ── Unix socket handler ───────────────────────────────────

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername") or "unix-client"
        logger.debug("New connection from %s", peer)
        try:
            while True:
                length_bytes = await reader.readexactly(4)
                length = struct.unpack(">I", length_bytes)[0]
                if length > 10 * 1024 * 1024:  # 10MB safety limit
                    break
                payload = await reader.readexactly(length)
                request = SyscallRequest.from_json(payload)
                auth_err = self._check_auth(request, transport="unix")
                if auth_err:
                    response = SyscallResponse(
                        request_id=request.request_id,
                        success=False,
                        error=f"auth failed: {auth_err}",
                    )
                else:
                    response = await self._dispatch(request)
                resp_bytes = response.to_json().encode()
                writer.write(struct.pack(">I", len(resp_bytes)) + resp_bytes)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as e:
            logger.error("Connection error: %s", e)
        finally:
            writer.close()
            await writer.wait_closed()

    # ── HTTP handler (optional, for easier debugging/integration) ─

    async def _start_http(self) -> None:
        try:
            from aiohttp import web

            app = web.Application()
            app.router.add_post("/syscall", self._http_handler)
            app.router.add_get("/status", self._http_status)
            app.router.add_get("/health", self._http_health)
            app.router.add_get("/events", self._http_events_sse)
            app.router.add_post("/reload", self._http_reload)

            runner = web.AppRunner(app)
            await runner.setup()
            self._http_runner = runner
            site = web.TCPSite(runner, "127.0.0.1", self.config.http_port)
            await site.start()
            logger.info("HTTP API listening on http://127.0.0.1:%d", self.config.http_port)
        except ImportError:
            logger.info("aiohttp not installed — HTTP API disabled")

    async def _http_handler(self, request) -> Any:
        from aiohttp import web
        try:
            body = await request.json()
            req = SyscallRequest(**body)
            http_token = request.headers.get("X-Agent-Token")
            auth_err = self._check_auth(req, transport="http", http_token=http_token)
            if auth_err:
                return web.json_response(
                    {"error": f"auth failed: {auth_err}"}, status=401,
                )
            response = await self._dispatch(req)
            return web.json_response(json.loads(response.to_json()))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _http_status(self, request) -> Any:
        from aiohttp import web
        return web.json_response(self.kernel.status())

    async def _http_health(self, request) -> Any:
        from aiohttp import web
        return web.json_response({"status": "ok"})

    async def _http_reload(self, request) -> Any:
        """Hot-reload config without restarting the daemon."""
        from aiohttp import web
        http_token = request.headers.get("X-Agent-Token")
        if self.config.require_auth and self._auth_token:
            if not http_token or not hmac.compare_digest(http_token, self._auth_token):
                return web.json_response(
                    {"error": "auth failed: invalid or missing X-Agent-Token"},
                    status=401,
                )
        try:
            result = await self.kernel.reload_config()
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _http_events_sse(self, request) -> Any:
        """SSE endpoint on the daemon HTTP port — lets standalone dashboard proxy events."""
        from aiohttp import web

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

        event_bus = self.kernel.get_event_bus()
        if not event_bus:
            await self._sse_write(resp, b"event: error\ndata: {\"msg\":\"No EventBus available\"}\n\n")
            return resp

        queue = event_bus.subscribe(replay_buffer=False)
        try:
            recent = event_bus.recent_events(limit=50)
            for evt in recent:
                line = json.dumps(evt, default=str)
                if not await self._sse_write(resp, f"event: {evt['type']}\ndata: {line}\n\n".encode()):
                    return resp

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    if not await self._sse_write(resp, event.to_sse().encode()):
                        break
                except asyncio.TimeoutError:
                    if not await self._sse_write(resp, b": keepalive\n\n"):
                        break
                except (ConnectionResetError, web.HTTPException):
                    break
        finally:
            event_bus.unsubscribe(queue)

        return resp

    async def _sse_write(self, resp, payload: bytes) -> bool:
        """Write to an SSE stream; return False if the browser already closed it."""
        try:
            await resp.write(payload)
            return True
        except (ConnectionResetError, BrokenPipeError):
            return False
        except Exception as e:
            if e.__class__.__name__ == "ClientConnectionResetError":
                return False
            raise

    # ── Core dispatch ─────────────────────────────────────────

    async def _dispatch(self, request: SyscallRequest) -> SyscallResponse:
        start = time.time()

        # Fast-path for ping
        if request.call_type == SyscallType.SYS_PING:
            return SyscallResponse(
                request_id=request.request_id,
                success=True,
                data={"pong": True, "pid": os.getpid()},
                elapsed_ms=(time.time() - start) * 1000,
            )

        # Fast-path for status
        if request.call_type == SyscallType.SYS_STATUS:
            return SyscallResponse(
                request_id=request.request_id,
                success=True,
                data=self.kernel.status(),
                elapsed_ms=(time.time() - start) * 1000,
            )

        # Model settings are control-plane reads/writes. Handle them before the
        # scheduler so a busy scan/chat queue cannot block the settings UI.
        if request.call_type == SyscallType.SETTINGS_MODEL_GET:
            return await self._handle_settings_model_get(request, start)
        if request.call_type == SyscallType.SETTINGS_MODEL:
            return await self._handle_settings_model(request, start)

        # Map syscall → agent task
        agent_name = SYSCALL_TO_AGENT.get(request.call_type)
        if not agent_name:
            return SyscallResponse(
                request_id=request.request_id,
                success=False,
                error=f"Unknown syscall: {request.call_type}",
                elapsed_ms=(time.time() - start) * 1000,
            )

        # Build task
        task = AgentTask(
            name=agent_name,
            priority=request.priority,
            input_data=self._build_input(request),
            caller=request.caller,
        )

        context = {
            "memory": self.kernel.get_memory(),
            "scheduler": self.kernel.get_scheduler(),
            "filesystem": self.kernel.get_filesystem(),
            "kernel": self.kernel,
            "llm": self.kernel.get_llm(),
            "event_bus": self.kernel.get_event_bus(),
        }

        # Submit and wait
        scheduler = self.kernel.get_scheduler()
        completed = await scheduler.submit_and_wait(task, context)

        elapsed_ms = (time.time() - start) * 1000
        if completed.state.value == "completed":
            return SyscallResponse(
                request_id=request.request_id,
                success=True,
                data=completed.result,
                elapsed_ms=elapsed_ms,
            )
        else:
            return SyscallResponse(
                request_id=request.request_id,
                success=False,
                error=completed.error or "Unknown error",
                elapsed_ms=elapsed_ms,
            )

    def _build_input(self, request: SyscallRequest) -> dict:
        """Merge syscall-specific parameters into task input."""
        params = dict(request.params)

        if request.call_type in (SyscallType.CONTEXT_SAVE, SyscallType.CONTEXT_LOAD):
            if request.call_type == SyscallType.CONTEXT_SAVE:
                params["action"] = "save"
            else:
                params["action"] = "load"

        # Map report syscall subtypes to report_type input
        if request.call_type == SyscallType.REPORT_DAILY:
            params["report_type"] = "daily"
        elif request.call_type == SyscallType.REPORT_PROJECT:
            params["report_type"] = "project"
        elif request.call_type == SyscallType.REPORT_BRIEF:
            params["report_type"] = "brief"

        assistant_actions = {
            SyscallType.ASSISTANT_CHAT: "chat",
            SyscallType.ASSISTANT_INSIGHTS: "insights",
            SyscallType.PROFILE_SUMMARY: "profile_summary",
            SyscallType.MEMORY_REVIEW: "memory_review",
            SyscallType.MEMORY_FEEDBACK: "memory_feedback",
            SyscallType.SOURCES_GET: "sources_get",
            SyscallType.SOURCES_CONFIGURE: "sources_configure",
            SyscallType.SETTINGS_MODEL: "settings_model",
            SyscallType.ONBOARDING_BOOTSTRAP: "onboarding_bootstrap",
            SyscallType.ASSISTANT_FIRST_INSIGHT: "onboarding_bootstrap",
        }
        if request.call_type in assistant_actions:
            params["action"] = assistant_actions[request.call_type]

        return params

    async def _handle_settings_model(self, request: SyscallRequest, start: float) -> SyscallResponse:
        try:
            from src.kernel.user_settings import save_model_settings

            result = save_model_settings(request.params)
            if request.params.get("scope") == "desktop":
                result["reload_skipped"] = "desktop scope does not change backend router"
            else:
                try:
                    reload_result = await self.kernel.reload_config()
                    result["reload"] = reload_result
                except Exception as e:
                    result["reload_error"] = str(e)
            return SyscallResponse(
                request_id=request.request_id,
                success=True,
                data=result,
                elapsed_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return SyscallResponse(
                request_id=request.request_id,
                success=False,
                error=str(e),
                elapsed_ms=(time.time() - start) * 1000,
            )

    async def _handle_settings_model_get(self, request: SyscallRequest, start: float) -> SyscallResponse:
        try:
            from src.kernel.user_settings import load_model_settings

            return SyscallResponse(
                request_id=request.request_id,
                success=True,
                data=load_model_settings(),
                elapsed_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return SyscallResponse(
                request_id=request.request_id,
                success=False,
                error=str(e),
                elapsed_ms=(time.time() - start) * 1000,
            )
