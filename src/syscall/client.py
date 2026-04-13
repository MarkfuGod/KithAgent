"""
Syscall Client SDK — how external agents connect to SysAgent.

This is the library that Cursor, Claude Code, or any custom agent
imports to talk to the running SysAgent daemon.

Usage:
    from agent_sys.client import SysAgentClient

    async with SysAgentClient() as client:
        # Search files
        results = await client.file_search("database config")

        # Read file info
        info = await client.file_read("/path/to/file.py")

        # Store knowledge
        await client.knowledge_store("preferences", "User prefers dark mode")

        # Save/load cross-session context
        await client.context_save("session-123", {"last_query": "..."})
        ctx = await client.context_load("session-123")

        # Check system status
        status = await client.status()
"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

from src.syscall.protocol import SyscallRequest, SyscallResponse, SyscallType


class SysAgentClient:
    """Client for communicating with the SysAgent daemon."""

    def __init__(self, socket_path: str = "/tmp/agent_sys.sock", caller: str = "cli"):
        self.socket_path = socket_path
        self.caller = caller
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_unix_connection(self.socket_path)

    async def close(self) -> None:
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()

    async def __aenter__(self) -> SysAgentClient:
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    # ── Low-level RPC ─────────────────────────────────────────

    async def _call(self, call_type: str, params: dict | None = None, priority: int = 1) -> SyscallResponse:
        if not self._writer or not self._reader:
            raise RuntimeError("Not connected — use 'async with SysAgentClient() as client:'")

        request = SyscallRequest(
            call_type=call_type,
            params=params or {},
            caller=self.caller,
            priority=priority,
        )
        payload = request.to_json().encode()
        self._writer.write(struct.pack(">I", len(payload)) + payload)
        await self._writer.drain()

        length_bytes = await self._reader.readexactly(4)
        length = struct.unpack(">I", length_bytes)[0]
        resp_bytes = await self._reader.readexactly(length)
        return SyscallResponse.from_json(resp_bytes)

    # ── High-level API ────────────────────────────────────────

    async def ping(self) -> dict:
        resp = await self._call(SyscallType.SYS_PING)
        return resp.data

    async def status(self) -> dict:
        resp = await self._call(SyscallType.SYS_STATUS)
        return resp.data

    async def file_search(self, query: str, file_type: str | None = None, limit: int = 20) -> list[dict]:
        params: dict[str, Any] = {"query": query, "limit": limit}
        if file_type:
            params["file_type"] = file_type
        resp = await self._call(SyscallType.FILE_SEARCH, params)
        if resp.success:
            return resp.data.get("matches", [])
        raise RuntimeError(resp.error)

    async def file_read(self, path: str) -> dict | None:
        resp = await self._call(SyscallType.FILE_READ, {"path": path})
        if resp.success:
            return resp.data
        raise RuntimeError(resp.error)

    async def knowledge_query(self, category: str | None = None, limit: int = 50) -> list[dict]:
        resp = await self._call(SyscallType.KNOWLEDGE_QUERY, {"category": category, "limit": limit})
        if resp.success:
            return resp.data.get("entries", [])
        raise RuntimeError(resp.error)

    async def knowledge_store(self, category: str, content: str, **kwargs) -> bool:
        params = {"category": category, "content": content, **kwargs}
        resp = await self._call(SyscallType.KNOWLEDGE_STORE, params)
        return resp.success

    async def context_save(self, session_id: str, context_data: dict, ttl: float = 3600) -> bool:
        resp = await self._call(SyscallType.CONTEXT_SAVE, {
            "session_id": session_id, "context_data": context_data, "ttl": ttl,
        })
        return resp.success

    async def context_load(self, session_id: str) -> dict | None:
        resp = await self._call(SyscallType.CONTEXT_LOAD, {"session_id": session_id})
        if resp.success:
            return resp.data.get("context")
        return None

    # ── v0.2: Smart Agent APIs ────────────────────────────────

    async def report_daily(self) -> dict:
        resp = await self._call(SyscallType.REPORT_DAILY)
        if resp.success:
            return resp.data
        raise RuntimeError(resp.error)

    async def report_project(self, project_dir: str | None = None) -> dict:
        params = {"project_dir": project_dir} if project_dir else {}
        resp = await self._call(SyscallType.REPORT_PROJECT, params)
        if resp.success:
            return resp.data
        raise RuntimeError(resp.error)

    async def report_brief(self) -> dict:
        """Get a context brief — 'everything a new agent session needs to know'."""
        resp = await self._call(SyscallType.REPORT_BRIEF)
        if resp.success:
            return resp.data
        raise RuntimeError(resp.error)

    async def profile_get(self) -> dict:
        resp = await self._call(SyscallType.PROFILE_GET)
        if resp.success:
            return resp.data
        raise RuntimeError(resp.error)

    async def analyze_behavior(self, hours: float = 168) -> dict:
        resp = await self._call(SyscallType.ANALYZE_BEHAVIOR, {"hours": hours})
        if resp.success:
            return resp.data
        raise RuntimeError(resp.error)

    async def classify_priority(self) -> dict:
        resp = await self._call(SyscallType.CLASSIFY_PRIORITY)
        if resp.success:
            return resp.data
        raise RuntimeError(resp.error)

    async def summarize_files(self, batch_size: int = 30) -> dict:
        resp = await self._call(SyscallType.FILE_SUMMARIZE, {"batch_size": batch_size})
        if resp.success:
            return resp.data
        raise RuntimeError(resp.error)


class SyncSysAgentClient:
    """Synchronous wrapper for environments that don't use asyncio (like Cursor plugins)."""

    def __init__(self, socket_path: str = "/tmp/agent_sys.sock", caller: str = "cli"):
        self._async_client = SysAgentClient(socket_path, caller)
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def connect(self) -> None:
        self._get_loop().run_until_complete(self._async_client.connect())

    def close(self) -> None:
        if self._loop:
            self._loop.run_until_complete(self._async_client.close())

    def __enter__(self) -> SyncSysAgentClient:
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def ping(self) -> dict:
        return self._get_loop().run_until_complete(self._async_client.ping())

    def status(self) -> dict:
        return self._get_loop().run_until_complete(self._async_client.status())

    def file_search(self, query: str, **kwargs) -> list[dict]:
        return self._get_loop().run_until_complete(self._async_client.file_search(query, **kwargs))

    def file_read(self, path: str) -> dict | None:
        return self._get_loop().run_until_complete(self._async_client.file_read(path))

    def knowledge_store(self, category: str, content: str, **kwargs) -> bool:
        return self._get_loop().run_until_complete(self._async_client.knowledge_store(category, content, **kwargs))

    def knowledge_query(self, category: str | None = None, limit: int = 50) -> list[dict]:
        return self._get_loop().run_until_complete(self._async_client.knowledge_query(category, limit))

    def context_save(self, session_id: str, data: dict, ttl: float = 3600) -> bool:
        return self._get_loop().run_until_complete(self._async_client.context_save(session_id, data, ttl))

    def context_load(self, session_id: str) -> dict | None:
        return self._get_loop().run_until_complete(self._async_client.context_load(session_id))

    # v0.2 smart agent methods
    def report_daily(self) -> dict:
        return self._get_loop().run_until_complete(self._async_client.report_daily())

    def report_project(self, project_dir: str | None = None) -> dict:
        return self._get_loop().run_until_complete(self._async_client.report_project(project_dir))

    def report_brief(self) -> dict:
        return self._get_loop().run_until_complete(self._async_client.report_brief())

    def profile_get(self) -> dict:
        return self._get_loop().run_until_complete(self._async_client.profile_get())

    def analyze_behavior(self, hours: float = 168) -> dict:
        return self._get_loop().run_until_complete(self._async_client.analyze_behavior(hours))

    def classify_priority(self) -> dict:
        return self._get_loop().run_until_complete(self._async_client.classify_priority())

    def summarize_files(self, batch_size: int = 30) -> dict:
        return self._get_loop().run_until_complete(self._async_client.summarize_files(batch_size))
