"""
FileSystem Watcher — the 'VFS' of AgentOS.

Watches configured directories for file changes and maintains
an up-to-date index in the Memory store. This is what allows
external agents to skip expensive file reads — they query the
pre-built index instead.

Two modes:
  1. Real-time: watchdog-based inotify/kqueue/FSEvents monitoring
  2. Periodic:  full scan at configurable intervals (fallback)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from fnmatch import fnmatch
from pathlib import Path

from src.kernel.config import FilesystemConfig
from src.memory.store import MemoryStore, content_hash

logger = logging.getLogger("agent_sys.filesystem")


class FileSystemWatcher:
    """Watches directories and indexes files into Memory."""

    def __init__(self, config: FilesystemConfig, memory: MemoryStore):
        self.config = config
        self.memory = memory
        self._running = False
        self._scan_task: asyncio.Task | None = None
        self._observer = None  # watchdog observer, lazily initialized
        self._stats = {"files_indexed": 0, "last_scan": 0.0}

    async def start(self) -> None:
        self._running = True
        # Initial full scan
        await self._full_scan()
        # Start periodic rescan in background
        self._scan_task = asyncio.create_task(self._periodic_scan_loop())
        # Try to start real-time watcher
        self._start_realtime_watcher()

    async def stop(self) -> None:
        self._running = False
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
        logger.info("FileSystem watcher stopped")

    # ── Full scan ─────────────────────────────────────────────

    async def _full_scan(self) -> None:
        logger.info("Starting full filesystem scan...")
        count = 0
        for watch_path in self.config.watch_paths:
            expanded = Path(os.path.expanduser(str(watch_path)))
            if not expanded.exists():
                logger.warning("Watch path does not exist: %s", expanded)
                continue
            count += await self._scan_directory(expanded)

        self._stats["files_indexed"] = count
        self._stats["last_scan"] = time.time()
        logger.info("Full scan complete: %d files indexed", count)

    async def _scan_directory(self, root: Path) -> int:
        count = 0
        max_size = self.config.max_file_size_mb * 1024 * 1024

        try:
            for entry in root.rglob("*"):
                if not self._running:
                    break
                if not entry.is_file():
                    continue
                if self._should_ignore(entry):
                    continue
                if entry.suffix not in self.config.index_extensions:
                    continue

                try:
                    stat = entry.stat()
                    if stat.st_size > max_size:
                        continue

                    existing = await self.memory.get_file_info(str(entry))
                    if existing and existing.get("modified_at") == stat.st_mtime:
                        count += 1
                        continue

                    content = entry.read_text(errors="replace")
                    chash = content_hash(content)

                    summary = self._extract_summary(entry, content)

                    await self.memory.upsert_file(
                        path=str(entry),
                        content_hash=chash,
                        size_bytes=stat.st_size,
                        modified_at=stat.st_mtime,
                        file_type=entry.suffix,
                        summary=summary,
                    )
                    count += 1
                except (PermissionError, OSError) as e:
                    logger.debug("Skip %s: %s", entry, e)
                except Exception as e:
                    logger.warning("Error indexing %s: %s", entry, e)

                # Yield to event loop periodically
                if count % 100 == 0:
                    await asyncio.sleep(0)

        except PermissionError:
            logger.debug("Permission denied: %s", root)

        return count

    def _should_ignore(self, path: Path) -> bool:
        parts = path.parts
        for pattern in self.config.ignore_patterns:
            if any(fnmatch(part, pattern) for part in parts):
                return True
            if fnmatch(path.name, pattern):
                return True
        return False

    def _extract_summary(self, path: Path, content: str) -> str:
        """Build a searchable summary: first N lines + structural info."""
        lines = content.split("\n")
        first_lines = "\n".join(lines[:30])

        parts = [f"[{path.suffix}] {path.name}"]

        if path.suffix == ".py":
            imports = [l for l in lines if l.startswith(("import ", "from "))]
            classes = [l.strip() for l in lines if l.strip().startswith("class ")]
            funcs = [l.strip() for l in lines if l.strip().startswith("def ")]
            if imports:
                parts.append(f"imports: {len(imports)}")
            if classes:
                parts.append(f"classes: {', '.join(c.split('(')[0].split(':')[0].replace('class ', '') for c in classes[:5])}")
            if funcs:
                parts.append(f"functions: {', '.join(f.split('(')[0].replace('def ', '') for f in funcs[:10])}")

        elif path.suffix in (".md", ".txt"):
            headings = [l for l in lines if l.startswith("#")]
            if headings:
                parts.append(f"headings: {'; '.join(h.strip() for h in headings[:5])}")

        elif path.suffix in (".json", ".yaml", ".yml"):
            parts.append(f"lines: {len(lines)}")

        parts.append(f"preview: {first_lines[:200]}")
        return " | ".join(parts)

    # ── Periodic scan ─────────────────────────────────────────

    async def _periodic_scan_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.config.scan_interval_seconds)
            if self._running:
                logger.debug("Periodic rescan triggered")
                await self._full_scan()

    # ── Real-time watcher (optional, uses watchdog) ───────────

    def _start_realtime_watcher(self) -> None:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            # Capture the running event loop from the main thread so
            # watchdog callbacks (which run in a background thread) can
            # schedule coroutines back onto it.
            loop = asyncio.get_running_loop()

            class _Handler(FileSystemEventHandler):
                def __init__(self, watcher: FileSystemWatcher):
                    self.watcher = watcher

                def on_modified(self, event):
                    if not event.is_directory:
                        asyncio.run_coroutine_threadsafe(
                            self.watcher._handle_file_event("modified", event.src_path),
                            loop,
                        )

                def on_created(self, event):
                    if not event.is_directory:
                        asyncio.run_coroutine_threadsafe(
                            self.watcher._handle_file_event("created", event.src_path),
                            loop,
                        )

                def on_deleted(self, event):
                    if not event.is_directory:
                        asyncio.run_coroutine_threadsafe(
                            self.watcher._handle_file_event("deleted", event.src_path),
                            loop,
                        )

            observer = Observer()
            handler = _Handler(self)
            for wp in self.config.watch_paths:
                expanded = Path(os.path.expanduser(str(wp)))
                if expanded.exists():
                    observer.schedule(handler, str(expanded), recursive=True)
            observer.start()
            self._observer = observer
            logger.info("Real-time file watcher started (watchdog)")
        except ImportError:
            logger.info("watchdog not installed — using periodic scan only")

    async def _handle_file_event(self, event_type: str, file_path: str) -> None:
        path = Path(file_path)
        if self._should_ignore(path):
            return
        if path.suffix not in self.config.index_extensions:
            return

        if event_type == "deleted":
            await self.memory.remove_file(str(path))
            logger.debug("Removed from index: %s", path)
        else:
            try:
                if not path.exists():
                    return
                stat = path.stat()
                if stat.st_size > self.config.max_file_size_mb * 1024 * 1024:
                    return
                content = path.read_text(errors="replace")
                await self.memory.upsert_file(
                    path=str(path),
                    content_hash=content_hash(content),
                    size_bytes=stat.st_size,
                    modified_at=stat.st_mtime,
                    file_type=path.suffix,
                    summary=self._extract_summary(path, content),
                )
                logger.debug("Indexed [%s]: %s", event_type, path)
            except Exception as e:
                logger.warning("Error handling %s event for %s: %s", event_type, path, e)

    def status(self) -> dict:
        return {
            "running": self._running,
            "realtime": self._observer is not None,
            **self._stats,
        }
