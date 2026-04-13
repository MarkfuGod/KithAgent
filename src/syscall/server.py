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
import json
import logging
import os
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

    async def start(self) -> None:
        socket_path = Path(str(self.config.auth_token_path)).parent.parent / "agent_sys.sock"
        actual_socket = str(self.kernel.config.kernel.socket_path)

        if os.path.exists(actual_socket):
            os.unlink(actual_socket)

        self._unix_server = await asyncio.start_unix_server(
            self._handle_connection, path=actual_socket,
        )
        os.chmod(actual_socket, 0o600)
        logger.info("Syscall server listening on %s", actual_socket)

        if self.config.transport == "http" or self.config.http_port:
            asyncio.create_task(self._start_http())

    async def stop(self) -> None:
        if self._unix_server:
            self._unix_server.close()
            await self._unix_server.wait_closed()

        socket_path = str(self.kernel.config.kernel.socket_path)
        if os.path.exists(socket_path):
            os.unlink(socket_path)

        if self._http_runner:
            await self._http_runner.cleanup()

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

        # Build context with kernel subsystem references
        context = {
            "memory": self.kernel.get_memory(),
            "scheduler": self.kernel.get_scheduler(),
            "filesystem": self.kernel.get_filesystem(),
            "kernel": self.kernel,
            "llm": self.kernel.get_llm(),
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

        return params
