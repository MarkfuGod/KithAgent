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
import hashlib
import logging
import os
import time
from fnmatch import fnmatch
from pathlib import Path

from src.kernel.config import FilesystemConfig
from src.memory.store import MemoryStore, content_hash

logger = logging.getLogger("agent_sys.filesystem")

_BINARY_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".doc", ".pptx", ".xlsx",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
})


def _next_walk(walker):
    try:
        return next(walker)
    except StopIteration:
        return None


class FileSystemWatcher:
    """Watches directories and indexes files into Memory."""

    def __init__(self, config: FilesystemConfig, memory: MemoryStore):
        self.config = config
        self.memory = memory
        self._running = False
        self._scan_task: asyncio.Task | None = None
        self._initial_scan_task: asyncio.Task | None = None
        self._observer = None  # watchdog observer, lazily initialized
        self._stats = {
            "files_indexed": 0,
            "last_scan": 0.0,
            "scan_in_progress": False,
            "scan_progress_files": 0,
        }

    async def start(self) -> None:
        self._running = True
        # Run initial scan in background — don't block boot
        self._initial_scan_task = asyncio.create_task(self._initial_scan_and_watch())

    async def _initial_scan_and_watch(self) -> None:
        """Run initial scan, then start periodic rescan + realtime watcher."""
        # Let the kernel finish bringing syscall/HTTP online before the
        # potentially expensive initial scan starts consuming event-loop time.
        await asyncio.sleep(1)
        try:
            await self._full_scan()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Initial filesystem scan failed: %s", e, exc_info=True)
        finally:
            self._stats["scan_in_progress"] = False

        if self._running:
            self._start_realtime_watcher()
            self._scan_task = asyncio.create_task(self._periodic_scan_loop())

    async def stop(self) -> None:
        self._running = False
        for task in (self._initial_scan_task, self._scan_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
        logger.info("FileSystem watcher stopped")

    # ── Full scan ─────────────────────────────────────────────

    async def _full_scan(self) -> None:
        logger.info("Starting full filesystem scan...")
        self._stats["scan_in_progress"] = True
        self._stats["scan_progress_files"] = 0
        count = 0
        try:
            for watch_path in self.config.watch_paths:
                expanded = Path(os.path.expanduser(str(watch_path)))
                if not await asyncio.to_thread(expanded.exists):
                    logger.warning("Watch path does not exist: %s", expanded)
                    continue
                count += await self._scan_directory(expanded)
        finally:
            self._stats["files_indexed"] = count
            self._stats["scan_progress_files"] = count
            self._stats["last_scan"] = time.time()
            self._stats["scan_in_progress"] = False
        logger.info("Full scan complete: %d files indexed", count)

    async def _scan_directory(self, root: Path) -> int:
        """Walk directory tree, pruning ignored dirs early to avoid wasting time."""
        count = 0
        visited = 0
        max_size = self.config.max_file_size_mb * 1024 * 1024
        extensions = set(self.config.index_extensions)

        try:
            walker = os.walk(str(root))
            while self._running:
                item = await asyncio.to_thread(_next_walk, walker)
                if item is None:
                    break
                dirpath, dirnames, filenames = item
                if not self._running:
                    break

                # Prune ignored directories IN-PLACE so os.walk skips them
                dirnames[:] = [
                    d for d in dirnames
                    if not self._should_ignore_dir(d, dirpath)
                ]

                for fname in filenames:
                    if not self._running:
                        break

                    visited += 1
                    if visited % 200 == 0:
                        self._stats["scan_progress_files"] = count
                        self._stats["files_indexed"] = count
                        await asyncio.sleep(0)

                    ext = os.path.splitext(fname)[1]
                    if ext not in extensions:
                        continue

                    full_path = os.path.join(dirpath, fname)

                    try:
                        stat = await asyncio.to_thread(os.stat, full_path)
                        if stat.st_size > max_size:
                            continue

                        existing = await self.memory.get_file_info(full_path)
                        if existing and existing.get("modified_at") == stat.st_mtime:
                            count += 1
                            continue

                        if ext in _BINARY_EXTENSIONS:
                            raw = await asyncio.to_thread(Path(full_path).read_bytes)
                            chash = hashlib.sha256(raw[:8192]).hexdigest()[:16]
                            summary = ""
                        else:
                            content = await asyncio.to_thread(
                                Path(full_path).read_text,
                                errors="replace",
                            )
                            chash = content_hash(content)
                            summary = self._extract_summary(Path(full_path), content)

                        await self.memory.upsert_file(
                            path=full_path,
                            content_hash=chash,
                            size_bytes=stat.st_size,
                            modified_at=stat.st_mtime,
                            file_type=ext,
                            summary=summary,
                        )
                        count += 1
                    except (PermissionError, OSError) as e:
                        logger.debug("Skip %s: %s", full_path, e)
                    except Exception as e:
                        logger.warning("Error indexing %s: %s", full_path, e)

        except PermissionError:
            logger.debug("Permission denied: %s", root)

        self._stats["scan_progress_files"] = count
        self._stats["files_indexed"] = count
        return count

    def _should_ignore_dir(self, dirname: str, parent: str) -> bool:
        """Fast check for directory pruning during os.walk — called on dir names only."""
        for pattern in self.config.ignore_patterns:
            if fnmatch(dirname, pattern):
                return True

        # Path-aware subpath patterns (e.g. ".cursor/extensions") — check the
        # full path so we can prune third-party trees inside otherwise-useful
        # hidden dirs like ~/.cursor.
        full = os.path.join(parent, dirname)
        subpaths = getattr(self.config, "ignore_subpaths", None) or []
        for sub in subpaths:
            needle = sub.strip("/")
            if not needle:
                continue
            if needle in full:
                return True

        # Privacy guard: never traverse hidden dot-directories directly under
        # $HOME (e.g. ~/.ssh, ~/.gnupg, ~/.aws) UNLESS the directory name is
        # in `filesystem.allowed_hidden_home_dirs` from config. Defaults there
        # cover common AI-tool config dirs (.cursor / .claude / .codex /
        # .continue / .aider) so their user-authored rules & memory files
        # get indexed; Linux users who keep personal configs under ~/.config
        # or ~/.local/share can opt those in explicitly.
        #
        # Semantic noise that lives INSIDE an allowed dir (e.g.
        # .cursor/extensions/<plugin>/) is NOT pruned here — it's indexed
        # and then skipped by the triage agent's rule-based pass, so the
        # dashboard can show what was filtered and why.
        home = str(Path.home())
        if parent == home and dirname.startswith("."):
            allowed = getattr(self.config, "allowed_hidden_home_dirs", None) or []
            if dirname not in allowed:
                return True

        return False

    def _should_ignore(self, path: Path) -> bool:
        """Full path check for realtime watcher events."""
        parts = path.parts
        for pattern in self.config.ignore_patterns:
            if any(fnmatch(part, pattern) for part in parts):
                return True
            if fnmatch(path.name, pattern):
                return True

        path_str = str(path)
        subpaths = getattr(self.config, "ignore_subpaths", None) or []
        for sub in subpaths:
            needle = sub.strip("/")
            if needle and needle in path_str:
                return True

        home = Path.home()
        try:
            rel = path.relative_to(home)
            top = rel.parts[0] if rel.parts else ""
            if top.startswith(".") and top not in (".cursor",):
                return True
        except ValueError:
            pass

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
                stat = await asyncio.to_thread(path.stat)
                if stat.st_size > self.config.max_file_size_mb * 1024 * 1024:
                    return

                ext = path.suffix.lower()
                if ext in _BINARY_EXTENSIONS:
                    raw = await asyncio.to_thread(path.read_bytes)
                    chash = hashlib.sha256(raw[:8192]).hexdigest()[:16]
                    summary = ""
                else:
                    content = await asyncio.to_thread(path.read_text, errors="replace")
                    chash = content_hash(content)
                    summary = self._extract_summary(path, content)

                await self.memory.upsert_file(
                    path=str(path),
                    content_hash=chash,
                    size_bytes=stat.st_size,
                    modified_at=stat.st_mtime,
                    file_type=ext,
                    summary=summary,
                )
                logger.debug("Indexed [%s]: %s", event_type, path)
            except Exception as e:
                logger.warning("Error handling %s event for %s: %s", event_type, path, e)

    def status(self) -> dict:
        return {
            "running": self._running,
            "realtime": self._observer is not None,
            "files_indexed": self._stats.get("files_indexed", 0),
            "last_scan": self._stats.get("last_scan", 0.0),
            "scan_in_progress": self._stats.get("scan_in_progress", False),
            "scan_progress_files": self._stats.get("scan_progress_files", 0),
        }
