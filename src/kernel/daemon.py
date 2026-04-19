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

        # EventBus — central pub/sub for real-time visibility
        from src.kernel.event_bus import EventBus
        event_bus = EventBus()
        self._subsystems["event_bus"] = event_bus
        logger.info("[boot] EventBus ready")

        # Boot order mirrors OS: memory → LLM → filesystem → scheduler → cron → syscall
        from src.memory.store import MemoryStore
        memory = MemoryStore(self.config.memory)
        await memory.initialize()
        self._subsystems["memory"] = memory
        logger.info("[boot] Memory store ready")

        # Scope hygiene: if the user narrowed watch_paths since the last run,
        # old file_index rows from a wider scan would otherwise be picked up
        # by summarizer/triage. Mark them as 'skip' once, up front, so the
        # working set stays in line with the current config.
        pruned = await memory.prune_out_of_scope(list(self.config.filesystem.watch_paths))
        if pruned:
            logger.warning(
                "[boot] Pruned %d file_index rows outside current watch_paths "
                "(marked as 'skip'). Narrow your scan scope? Those rows will "
                "no longer be summarized.",
                pruned,
            )

        from src.memory import embeddings as emb_module
        emb_cfg = self.config.memory.embedding
        emb_module.configure(
            provider=emb_cfg.provider,
            local_model=emb_cfg.local_model,
            api_key_env=emb_cfg.api_key_env,
            api_base_url=emb_cfg.api_base_url,
            model=emb_cfg.model,
            dimensions=emb_cfg.dimensions,
        )
        logger.info("[boot] Embedding engine ready (%s)", emb_module.get_provider_info())

        from src.llm.router import create_router_from_config
        llm_cfg = {
            "default_provider": self.config.llm.default_provider,
            "providers": self.config.llm.providers,
            "routing": self.config.llm.routing,
            "defaults": self.config.llm.defaults,
            "functions": self.config.llm.functions,
        }
        llm_router = create_router_from_config(llm_cfg)
        llm_router.set_event_bus(event_bus)
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
        """Graceful shutdown — reverse boot order. Safe to call multiple times."""
        if not self._running:
            return
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

    def get_event_bus(self):
        return self._subsystems.get("event_bus")

    async def reload_config(self) -> dict:
        """Hot-reload: re-read YAML config and rebuild the LLM router without full restart."""
        from src.kernel.config import load_config
        new_config = load_config()

        # Merge saved user overrides from ~/.agent_sys/llm_config.yaml
        self._apply_saved_llm_config(new_config)

        # Rebuild LLM router
        from src.llm.router import create_router_from_config
        llm_cfg = {
            "default_provider": new_config.llm.default_provider,
            "providers": new_config.llm.providers,
            "routing": new_config.llm.routing,
            "defaults": new_config.llm.defaults,
            "functions": new_config.llm.functions,
        }
        new_router = create_router_from_config(llm_cfg)
        event_bus = self.get_event_bus()
        if event_bus:
            new_router.set_event_bus(event_bus)
        self._subsystems["llm"] = new_router
        available = new_router.available_providers()

        # Rebuild embedding provider
        from src.memory import embeddings as emb_module
        emb_cfg = new_config.memory.embedding
        emb_module.configure(
            provider=emb_cfg.provider,
            local_model=emb_cfg.local_model,
            api_key_env=emb_cfg.api_key_env,
            api_base_url=emb_cfg.api_base_url,
            model=emb_cfg.model,
            dimensions=emb_cfg.dimensions,
        )

        # Update cron strategy
        cron = self._subsystems.get("cron")
        if cron and hasattr(cron, "config"):
            cron.config.adaptive.strategy = new_config.cron.adaptive.strategy

        self.config = new_config
        logger.info("Config reloaded: LLM providers=%s, embedding=%s, strategy=%s",
                     available, emb_cfg.provider, new_config.cron.adaptive.strategy)

        if event_bus:
            await event_bus.emit_dict("config.reloaded", {
                "llm_providers": available,
                "embedding_provider": emb_cfg.provider,
                "strategy": new_config.cron.adaptive.strategy,
            })

        return {
            "reloaded": True,
            "llm_providers": available,
            "embedding_provider": emb_cfg.provider,
            "strategy": new_config.cron.adaptive.strategy,
        }

    @staticmethod
    def _apply_saved_llm_config(config) -> None:
        """Merge persisted llm_config.yaml overrides into config (same logic as CLI boot)."""
        import yaml

        saved_path = Path(os.path.expanduser("~/.agent_sys/llm_config.yaml"))
        if not saved_path.exists():
            return
        try:
            with open(saved_path) as f:
                saved = yaml.safe_load(f) or {}
            if saved.get("default_provider"):
                config.llm.default_provider = saved["default_provider"]
            if saved.get("providers"):
                config.llm.providers.update(saved["providers"])
            for env_var, value in saved.get("env_vars", {}).items():
                os.environ.setdefault(env_var, value)
            logger.debug("Merged saved LLM config: provider=%s", saved.get("default_provider"))
        except Exception as e:
            logger.warning("Failed to load saved LLM config during reload: %s", e)

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
        result = {
            "name": self.config.kernel.name,
            "version": self.config.kernel.version,
            "pid": os.getpid(),
            "running": self._running,
            "subsystems": list(self._subsystems.keys()),
        }
        fs = self.get_filesystem()
        if fs:
            fs_status = fs.status()
            result["filesystem"] = {
                "scan_in_progress": fs_status.get("scan_in_progress", False),
                "files_indexed": fs_status.get("files_indexed", 0),
                "scan_progress": fs_status.get("scan_progress_files", 0),
                "realtime_watcher": fs_status.get("realtime", False),
            }
        return result
