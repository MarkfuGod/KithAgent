"""
Kernel Daemon — the 'init process' of AgentOS.

Responsibilities:
  1. Daemonize (detach from terminal, write PID file)
  2. Boot subsystems: FileSystem watcher, Memory store, Scheduler, Syscall API
  3. Run the main event loop
  4. Handle graceful shutdown (SIGTERM/SIGINT)

Analogy: This is 'systemd' / 'launchd' for agents.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from src.kernel.config import AgentOSConfig, load_config

logger = logging.getLogger("agent_sys.kernel")


class SysAgentKernel:
    """Core kernel — orchestrates all subsystems."""

    def __init__(self, config: AgentOSConfig | None = None):
        self.config = config or load_config()
        self._running = False
        self._subsystems: dict[str, object] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── lifecycle ──────────────────────────────────────────────

    async def boot(self) -> None:
        """Initialize all subsystems in dependency order."""
        logger.info("AgentOS Kernel v%s booting...", self.config.kernel.version)

        self._ensure_dirs()
        self._write_pid()
        self._install_signal_handlers()

        # Boot order mirrors OS: memory → LLM → filesystem → scheduler → cron → syscall
        from src.memory.store import MemoryStore
        memory = MemoryStore(self.config.memory)
        await memory.initialize()
        self._subsystems["memory"] = memory
        logger.info("[boot] Memory store ready")

        from src.llm.router import create_router_from_config
        llm_cfg = {
            "default_provider": self.config.llm.default_provider,
            "providers": self.config.llm.providers,
            "routing": self.config.llm.routing,
        }
        llm_router = create_router_from_config(llm_cfg)
        self._subsystems["llm"] = llm_router
        available = llm_router.available_providers()
        logger.info("[boot] LLM router ready (providers: %s)", available or "none — set API keys")

        from src.filesystem.watcher import FileSystemWatcher
        fs_watcher = FileSystemWatcher(self.config.filesystem, memory)
        await fs_watcher.start()
        self._subsystems["filesystem"] = fs_watcher
        logger.info("[boot] FileSystem watcher ready")

        from src.scheduler.pool import AgentScheduler
        scheduler = AgentScheduler(self.config.scheduler)
        await scheduler.start()
        self._subsystems["scheduler"] = scheduler
        logger.info("[boot] Scheduler ready")

        from src.kernel.cron import CronScheduler
        cron = CronScheduler(self.config.cron, kernel=self)
        await cron.start()
        self._subsystems["cron"] = cron
        logger.info("[boot] Cron scheduler ready")

        from src.syscall.server import SyscallServer
        api = SyscallServer(self.config.syscall, kernel=self)
        await api.start()
        self._subsystems["syscall"] = api
        logger.info("[boot] Syscall API ready")

        self._running = True
        logger.info("AgentOS Kernel boot complete. All subsystems online.")

    async def run(self) -> None:
        """Main event loop — keeps kernel alive and processes internal tasks."""
        await self.boot()
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Graceful shutdown — reverse boot order."""
        logger.info("Kernel shutting down...")
        self._running = False

        for name in reversed(list(self._subsystems)):
            sub = self._subsystems[name]
            if hasattr(sub, "stop"):
                logger.info("[shutdown] Stopping %s", name)
                await sub.stop()

        self._remove_pid()
        logger.info("Kernel shutdown complete.")

    # ── subsystem access (used by syscall handlers) ───────────

    def get_memory(self):
        return self._subsystems.get("memory")

    def get_filesystem(self):
        return self._subsystems.get("filesystem")

    def get_scheduler(self):
        return self._subsystems.get("scheduler")

    def get_llm(self):
        return self._subsystems.get("llm")

    # ── internal helpers ──────────────────────────────────────

    def _ensure_dirs(self) -> None:
        log_dir = Path(os.path.expanduser(str(self.config.kernel.log_file))).parent
        log_dir.mkdir(parents=True, exist_ok=True)

        data_dir = Path(os.path.expanduser("~/.agent_sys"))
        data_dir.mkdir(parents=True, exist_ok=True)

    def _write_pid(self) -> None:
        pid_path = Path(str(self.config.kernel.pid_file))
        pid_path.write_text(str(os.getpid()))
        logger.debug("PID %d written to %s", os.getpid(), pid_path)

    def _remove_pid(self) -> None:
        pid_path = Path(str(self.config.kernel.pid_file))
        pid_path.unlink(missing_ok=True)

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

    # ── status ────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "name": self.config.kernel.name,
            "version": self.config.kernel.version,
            "pid": os.getpid(),
            "running": self._running,
            "subsystems": list(self._subsystems.keys()),
        }
