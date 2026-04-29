"""
agent-sys CLI entry point.

See `agent-sys --help` for the full command list. Commands are grouped by
purpose (daemon lifecycle / query / manual runs / UI).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any

from src.cli_ui import CLIUI


def setup_logging(level: str = "INFO", *, console: bool = False) -> None:
    log_dir = Path.home() / ".agent_sys" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_dir / "sysagent.log", encoding="utf-8"),
    ]
    if console:
        handlers.insert(0, logging.StreamHandler(sys.stderr))

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def cmd_start(args: argparse.Namespace) -> None:
    """Start the SysAgent kernel."""
    from src.kernel.config import load_config
    from src.kernel.daemon import SysAgentKernel

    ui = CLIUI()
    config = load_config(args.config)
    setup_logging(config.kernel.log_level, console=bool(getattr(args, "verbose", False)))
    logger = logging.getLogger("agent_sys.cli")

    pid_file = Path(str(config.kernel.pid_file))
    force = bool(getattr(args, "force", False))
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)
            # Process is alive.
            if old_pid == os.getpid():
                # Stale PID file pointing at our own recycled PID — safe to clear.
                pid_file.unlink(missing_ok=True)
            elif force:
                logger.warning(
                    "--force given: terminating existing agent-sys (PID %d) before starting.",
                    old_pid,
                )
                os.kill(old_pid, signal.SIGTERM)
                import time as _time
                for _ in range(50):
                    _time.sleep(0.1)
                    try:
                        os.kill(old_pid, 0)
                    except ProcessLookupError:
                        break
                else:
                    os.kill(old_pid, signal.SIGKILL)
                    _time.sleep(0.5)
                pid_file.unlink(missing_ok=True)
            else:
                ui.print(
                    f"\nagent-sys is already running (PID {old_pid}).\n"
                    f"  - To see status:      agent-sys status\n"
                    f"  - To stop it first:   agent-sys stop\n"
                    f"  - To force restart:   agent-sys start --force\n"
                    f"\nRefusing to start a second instance — your previous daemon is still alive.\n"
                )
                sys.exit(1)
        except (ProcessLookupError, ValueError):
            pid_file.unlink(missing_ok=True)

    # Load any persisted scan-scope preferences from a previous first-run.
    config = _load_saved_scan_config(config)
    # Desktop/background starts are non-interactive, so saved model settings
    # must be applied before the optional first-run prompt gate.
    config = _load_saved_llm_config(config)

    # First-run UX: ask about scan scope + LLM before we actually boot.
    first_insight_payload: dict[str, Any] | None = None
    if not args.daemon:
        config = _prompt_scan_paths(config)
        config = _check_llm_and_prompt(config)
        first_insight_payload = _maybe_collect_start_first_insight(args, ui)

    if args.daemon:
        _daemonize()

    kernel = SysAgentKernel(config)

    try:
        if args.daemon:
            asyncio.run(kernel.run())
        else:
            asyncio.run(_run_kernel_foreground(kernel, ui, first_insight_payload))
    except KeyboardInterrupt:
        ui.print("\nShutdown requested.")


def cmd_stop(args: argparse.Namespace) -> None:
    """Stop the running SysAgent daemon."""
    from src.kernel.config import load_config

    config = load_config(args.config)
    pid_file = Path(str(config.kernel.pid_file)).expanduser()
    if not pid_file.exists():
        print("SysAgent is not running.")
        return

    try:
        pid = int(pid_file.read_text().strip())
        # First try graceful SIGTERM
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to SysAgent (PID {pid}), waiting...")

        # Wait up to 5 seconds for the process to exit
        import time as _time
        for _ in range(50):
            _time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                print("SysAgent stopped.")
                pid_file.unlink(missing_ok=True)
                return

        # Process didn't exit — force kill (handles suspended/stuck processes)
        print(f"Process {pid} didn't exit, sending SIGKILL...")
        os.kill(pid, signal.SIGKILL)
        _time.sleep(0.5)
        pid_file.unlink(missing_ok=True)
        print("SysAgent force-killed.")

    except ProcessLookupError:
        print("SysAgent process not found. Cleaning up PID file.")
        pid_file.unlink(missing_ok=True)
    except Exception as e:
        print(f"Error stopping SysAgent: {e}")
        pid_file.unlink(missing_ok=True)


def cmd_status(args: argparse.Namespace) -> None:
    """Show system status via syscall."""
    asyncio.run(_async_status(json_output=bool(getattr(args, "json", False))))


async def _async_status(json_output: bool = False) -> None:
    from src.syscall.client import SysAgentClient
    ui = CLIUI(json_output=json_output)
    try:
        async with SysAgentClient() as client:
            status = await client.status()
            if json_output:
                print(json.dumps(status, indent=2, ensure_ascii=False))
            else:
                _print_backend_status(status, ui)
    except (ConnectionRefusedError, FileNotFoundError):
        ui.print("SysAgent is not running. Start with: agent-sys start")


def cmd_home(args: argparse.Namespace) -> None:
    """Show the Kith command center."""
    from src.kernel.config import load_config

    config = load_config(args.config)
    status = asyncio.run(_fetch_daemon_status())
    _print_home(CLIUI(), config, status)


def cmd_doctor(args: argparse.Namespace) -> None:
    """Run local environment and daemon checks."""
    from src.kernel.config import load_config

    config = load_config(args.config)
    status = asyncio.run(_fetch_daemon_status())
    _print_doctor(CLIUI(), config, status)


def cmd_statusline(args: argparse.Namespace) -> None:
    """Print a compact one-line status for shell prompts/status bars."""
    status = asyncio.run(_fetch_daemon_status(timeout=1.2))
    print(_format_statusline(status))


async def _fetch_daemon_status(timeout: float = 2.0) -> dict | None:
    from src.syscall.client import SysAgentClient

    try:
        async with SysAgentClient() as client:
            return await asyncio.wait_for(client.status(), timeout=timeout)
    except Exception:
        return None


def _print_home(ui: CLIUI, config, status: dict | None) -> None:
    online = bool(status and status.get("running"))
    fs = (status or {}).get("filesystem") or {}
    memory = (status or {}).get("memory") or {}
    llm = (status or {}).get("llm") or {}
    insight = (status or {}).get("first_insight") or {}
    ui.hero(
        "Kith Agent",
        "A quiet local memory backend for your personal agents.",
        [
            ("status", "online" if online else "offline"),
            ("socket", config.kernel.socket_path),
            ("files", memory.get("indexed_files", fs.get("files_indexed", 0))),
            ("model", ", ".join(llm.get("available_providers") or []) or ("unknown" if online else "not connected")),
            ("first insight", "ready" if insight.get("ready") else "pending"),
        ],
    )
    ui.cards(
        "Command Center",
        [
            ("Start", "Bring the local memory daemon online.", "agent-sys start"),
            ("First Insight", "Optional: helps Kith understand you faster.", "agent-sys first-insight"),
            ("Doctor", "Check daemon, model, logs, socket, and privacy basics.", "agent-sys doctor"),
            ("Status", "Read the backend state without scrolling logs.", "agent-sys status"),
            ("Dashboard", "Open the browser control panel.", "agent-sys dashboard"),
            ("Logs", "Follow details in a separate terminal.", "agent-sys logs -f"),
            ("Search", "Ask Kith's index where something lives.", 'agent-sys search "meeting notes"'),
            ("Brief", "Give a new agent session the short version of you.", "agent-sys report brief"),
            ("Statusline", "Use one compact line in your shell prompt.", "agent-sys statusline"),
        ],
    )
    ui.info("Tip: keep `agent-sys start` clean, and open `agent-sys logs -f` in another terminal when debugging.")


def _print_doctor(ui: CLIUI, config, status: dict | None) -> None:
    log_path = Path(str(config.kernel.log_file)).expanduser()
    token_path = Path(str(config.syscall.auth_token_path)).expanduser()
    online = bool(status and status.get("running"))
    llm = (status or {}).get("llm") or {}
    embedding = (status or {}).get("embedding") or {}
    first = (status or {}).get("first_insight") or {}
    fs = (status or {}).get("filesystem") or {}
    rows = [
        ("python", sys.version.split()[0], _doctor_state("ok")),
        ("daemon", f"pid={status.get('pid')}" if online and status else "offline", _doctor_state("ok" if online else "warn")),
        ("socket", str(config.kernel.socket_path), _doctor_state("ok" if Path(str(config.kernel.socket_path)).exists() else "warn")),
        ("http", f"127.0.0.1:{config.syscall.http_port}", _doctor_state("ok" if online else "warn")),
        ("auth token", str(token_path), _doctor_state("ok" if token_path.exists() else "warn")),
        ("logs", str(log_path), _doctor_state("ok" if log_path.exists() else "warn")),
        ("llm", ", ".join(llm.get("available_providers") or []) or "none", _doctor_state("ok" if llm.get("available_providers") else "warn")),
        ("embedding", embedding.get("provider", "unknown"), _doctor_state("ok" if embedding.get("available") else "warn")),
        ("filesystem", "scanning" if fs.get("scan_in_progress") else "idle", _doctor_state("ok" if online else "warn")),
        ("first insight", "ready" if first.get("ready") else "pending", _doctor_state("ok" if first.get("ready") else "warn")),
    ]
    ui.table("Doctor", ["check", "value", "state"], rows)
    if not online:
        ui.info("Start the backend with: agent-sys start")
    elif not llm.get("available_providers"):
        ui.warning("No LLM provider is available. Run `agent-sys start` interactively or edit ~/.agent_sys/llm_config.yaml.")


def _doctor_state(state: str) -> str:
    if state == "ok":
        return "[ok] ok"
    if state == "warn":
        return "[warn] needs attention"
    return state


def _format_statusline(status: dict | None) -> str:
    if not status or not status.get("running"):
        return "Kith offline"
    memory = status.get("memory") or {}
    fs = status.get("filesystem") or {}
    llm = status.get("llm") or {}
    first = status.get("first_insight") or {}
    files = memory.get("indexed_files", fs.get("files_indexed", 0))
    providers = llm.get("available_providers") or []
    provider = providers[0] if providers else "no-llm"
    insight = "insight:ready" if first.get("ready") else "insight:pending"
    scan = "scan:running" if fs.get("scan_in_progress") else "scan:idle"
    return f"Kith online pid:{status.get('pid')} files:{files} {provider} {insight} {scan}"


def cmd_ping(args: argparse.Namespace) -> None:
    asyncio.run(_async_ping())


async def _async_ping() -> None:
    from src.syscall.client import SysAgentClient
    try:
        async with SysAgentClient() as client:
            result = await client.ping()
            print(f"pong! (PID: {result.get('pid')})")
    except (ConnectionRefusedError, FileNotFoundError):
        print("SysAgent is not running.")


def cmd_logs(args: argparse.Namespace) -> None:
    """Show daemon logs, optionally following in real time."""
    from src.kernel.config import load_config

    config = load_config(args.config)
    log_path = Path(str(config.kernel.log_file)).expanduser()
    _print_logs(log_path, lines=int(args.lines), follow=bool(args.follow))


def _print_logs(log_path: Path, *, lines: int = 120, follow: bool = False) -> None:
    ui = CLIUI()
    if not log_path.exists():
        ui.print(f"No log file yet: {log_path}")
        ui.print("Start the daemon first: agent-sys start")
        return

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        if lines > 0:
            recent = f.readlines()[-lines:]
            for line in recent:
                print(line.rstrip("\n"))
        if not follow:
            return
        ui.info(f"Following {log_path}. Press Ctrl-C to stop.")
        f.seek(0, os.SEEK_END)
        try:
            while True:
                line = f.readline()
                if line:
                    print(line.rstrip("\n"))
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            ui.print("\nStopped following logs.")


async def _run_kernel_foreground(kernel, ui: CLIUI, first_insight_payload: dict[str, Any] | None) -> None:
    """Boot with CLI progress, optionally run First Insight, then stay alive."""
    with ui.status("Booting Kith backend: memory, scheduler, filesystem, syscall API..."):
        await kernel.boot()
    _print_start_banner(ui, kernel.config)
    ui.success("Daemon is online. Initial file scan continues in the background.")
    ui.info("Open the control panel with: agent-sys dashboard")

    if first_insight_payload:
        asyncio.create_task(_run_optional_first_insight(
            first_insight_payload,
            ui,
            socket_path=str(kernel.config.kernel.socket_path),
        ))
        ui.info("First Insight is running in the background; normal daemon features are already available.")

    try:
        while kernel._running:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await kernel.shutdown()


def _print_start_banner(ui: CLIUI, config) -> None:
    ui.hero(
        "Kith backend is online",
        f"agent-sys v{config.kernel.version}",
        [
            ("pid", os.getpid()),
            ("socket", config.kernel.socket_path),
            ("http", f"http://127.0.0.1:{config.syscall.http_port}"),
            ("logs", "~/.agent_sys/logs/sysagent.log"),
            ("panel", "agent-sys dashboard"),
            ("next", "agent-sys doctor  |  agent-sys logs -f"),
        ],
    )


def _maybe_collect_start_first_insight(args: argparse.Namespace, ui: CLIUI) -> dict[str, Any] | None:
    mode = getattr(args, "first_insight", None)
    if mode is False:
        return None
    if not sys.stdin.isatty():
        if mode is True:
            ui.warning("Cannot run interactive First Insight because stdin is not a TTY.")
        return None
    if mode is None:
        should_run = ui.confirm(
            "Run optional First Insight now? It helps Kith understand you faster, but skipping keeps all normal features available.",
            default=True,
        )
        if not should_run:
            ui.info("No problem. Core indexing, search, reports, and dashboard still work. Run later with: agent-sys first-insight")
            return None
    return _prompt_first_insight_payload(ui)


_ROLE_OPTIONS = [
    "student / learner",
    "researcher",
    "creator",
    "writer",
    "designer / artist",
    "developer / engineer",
    "product / business",
    "teacher / mentor",
    "founder / freelancer",
    "manager / operator",
    "caregiver / parent",
    "life organizer",
]

_GOAL_OPTIONS = [
    "plan my day or week",
    "remember what matters about me",
    "organize files and notes",
    "find past notes quickly",
    "support study or research",
    "manage projects and deadlines",
    "prepare work or school tasks",
    "track habits and routines",
    "reflect on mood and energy",
    "capture creative ideas",
    "reduce digital clutter",
    "suggest next actions gently",
]

_INTEREST_OPTIONS = [
    "reading / books",
    "music",
    "film / video",
    "games",
    "fitness / health",
    "food / cooking",
    "travel",
    "finance / business",
    "design / art",
    "writing",
    "language learning",
    "research / learning",
    "AI tools",
    "programming",
]

_FOCUS_OPTIONS = [
    "today's priorities",
    "this week's plan",
    "a work project",
    "a school or research task",
    "a creative project",
    "personal knowledge base",
    "file cleanup",
    "health / life routine",
    "learning plan",
    "career / portfolio",
    "family / home logistics",
    "reducing overwhelm",
]


def _prompt_first_insight_payload(ui: CLIUI) -> dict[str, Any]:
    ui.hero(
        "First Insight",
        "Pick presets or write your own. Nothing here is permanent.",
        [
            ("privacy", "browser metadata is optional; no cookies, sessions, tokens, or page bodies"),
            ("memory", "saved facts stay correctable later"),
            ("optional", "skipping does not block search, reports, dashboard, or indexing"),
        ],
    )
    answers = {
        "roles": _choose_many(ui, "What roles describe you?", _ROLE_OPTIONS),
        "goals": _choose_many(ui, "What should Kith help with?", _GOAL_OPTIONS),
        "interests": _choose_many(ui, "What interests should Kith notice?", _INTEREST_OPTIONS),
        "current_focus": _choose_many(ui, "What is your current focus?", _FOCUS_OPTIONS),
        "planning_style": _choose_one(ui, "Planning style", ["lightweight", "balanced", "detailed", "quiet"]),
        "suggestion_cadence": _choose_one(ui, "Suggestion cadence", ["daily", "weekly", "quiet"]),
    }
    include_browser = ui.confirm(
        "Allow aggregated browser titles/domains/download metadata? No cookies, sessions, tokens, or page bodies are read.",
        default=False,
    )
    return {
        "answers": _normalize_first_insight_answers(answers),
        "include_browser_history": include_browser,
        "history_days": 30,
        "history_limit": 500,
    }


def _choose_many(ui: CLIUI, label: str, options: list[str]) -> list[str]:
    ui.choice_grid(label, options)
    raw = ui.prompt("Choose numbers separated by commas, or o", default="")
    selected: list[str] = []
    wants_custom = False
    for token in re.split(r"[\s,，;；/]+", raw):
        cleaned = token.strip().lower()
        if not cleaned:
            continue
        if cleaned in {"o", "other", "custom"}:
            wants_custom = True
            continue
        if cleaned.isdigit():
            idx = int(cleaned)
            if 1 <= idx <= len(options):
                selected.append(options[idx - 1])
                continue
        selected.extend(_split_cli_values(cleaned))
    if wants_custom or not selected:
        custom = ui.prompt("Other / custom (comma-separated, blank to skip)", default="")
        selected.extend(_split_cli_values(custom))
    return list(dict.fromkeys(item for item in selected if item))


def _choose_one(ui: CLIUI, label: str, options: list[str]) -> str:
    ui.print(f"\n{label}")
    for idx, option in enumerate(options, 1):
        ui.print(f"  [{idx}] {option}")
    raw = ui.prompt("Choose one", default="1").strip().lower()
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    if raw in options:
        return raw
    return options[0]


def _split_cli_values(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_items = [values]
    else:
        raw_items = [str(item) for item in values]
    result: list[str] = []
    for item in raw_items:
        for part in re.split(r"[\n,，;；/]+", item):
            cleaned = part.strip()
            if cleaned and cleaned not in result:
                result.append(cleaned)
    return result


def _normalize_first_insight_answers(answers: dict[str, Any]) -> dict[str, Any]:
    cadence = str(answers.get("suggestion_cadence") or "daily").strip().lower()
    if cadence not in {"daily", "weekly", "quiet"}:
        cadence = "daily"
    planning_style = str(answers.get("planning_style") or "lightweight").strip() or "lightweight"
    return {
        "roles": _split_cli_values(answers.get("roles")),
        "goals": _split_cli_values(answers.get("goals")),
        "interests": _split_cli_values(answers.get("interests")),
        "current_focus": _split_cli_values(answers.get("current_focus")),
        "planning_style": planning_style,
        "suggestion_cadence": cadence,
    }


def _first_insight_payload_from_args(args: argparse.Namespace, ui: CLIUI) -> dict[str, Any]:
    answers = _normalize_first_insight_answers({
        "roles": getattr(args, "roles", []),
        "goals": getattr(args, "goals", []),
        "interests": getattr(args, "interests", []),
        "current_focus": getattr(args, "current_focus", []),
        "planning_style": getattr(args, "planning_style", "lightweight"),
        "suggestion_cadence": getattr(args, "suggestion_cadence", "daily"),
    })

    has_answers = any(answers[key] for key in ("roles", "goals", "interests", "current_focus"))
    interactive = (
        not bool(getattr(args, "yes", False))
        and not bool(getattr(args, "json", False))
        and sys.stdin.isatty()
    )
    if interactive and not has_answers:
        return _prompt_first_insight_payload(ui)

    include_browser = getattr(args, "include_browser_history", None)
    if include_browser is None:
        include_browser = False
        if interactive:
            include_browser = ui.confirm(
                "Allow aggregated browser titles/domains/download metadata?",
                default=False,
            )
    return {
        "answers": answers,
        "include_browser_history": bool(include_browser),
        "history_days": int(getattr(args, "history_days", 30)),
        "history_limit": int(getattr(args, "history_limit", 500)),
    }


def cmd_first_insight(args: argparse.Namespace) -> None:
    asyncio.run(_async_first_insight(args))


async def _async_first_insight(args: argparse.Namespace) -> None:
    ui = CLIUI(json_output=bool(getattr(args, "json", False)))
    payload = _first_insight_payload_from_args(args, ui)
    try:
        result = await _run_first_insight_payload(payload, ui)
        if getattr(args, "json", False):
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            _print_first_insight_result(result, ui)
    except (ConnectionRefusedError, FileNotFoundError):
        ui.print("SysAgent is not running. Start with: agent-sys start")


async def _run_first_insight_payload(
    payload: dict[str, Any],
    ui: CLIUI,
    *,
    socket_path: str = "/tmp/agent_sys.sock",
    show_status: bool = True,
) -> dict:
    from src.syscall.client import SysAgentClient

    status_message = "Generating First Insight from answers, local index, and authorized sources..."
    if not show_status:
        async with SysAgentClient(socket_path=socket_path) as client:
            return await client.first_insight(
                payload["answers"],
                include_browser_history=bool(payload.get("include_browser_history")),
                history_days=int(payload.get("history_days", 30)),
                history_limit=int(payload.get("history_limit", 500)),
            )
    with ui.status(status_message):
        async with SysAgentClient(socket_path=socket_path) as client:
            return await client.first_insight(
                payload["answers"],
                include_browser_history=bool(payload.get("include_browser_history")),
                history_days=int(payload.get("history_days", 30)),
                history_limit=int(payload.get("history_limit", 500)),
            )


async def _run_optional_first_insight(
    payload: dict[str, Any],
    ui: CLIUI,
    *,
    socket_path: str,
) -> None:
    try:
        result = await _run_first_insight_payload(payload, ui, socket_path=socket_path, show_status=False)
        ui.success("First Insight completed. Kith will use this to personalize suggestions faster.")
        _print_first_insight_result(result, ui)
    except Exception as e:
        ui.error(f"First Insight failed: {e}")
        ui.info("The daemon is still running; retry when convenient with: agent-sys first-insight")


def _print_first_insight_result(result: dict, ui: CLIUI) -> None:
    profile = result.get("profile") or {}
    identity = profile.get("identity") if isinstance(profile, dict) else {}
    summary = identity.get("summary") if isinstance(identity, dict) else ""
    browser = result.get("browser_history") or {}
    ui.panel(
        "First Insight Complete",
        [
            f"Run: {result.get('run_id', '?')}",
            f"Elapsed: {result.get('elapsed_seconds', 0)}s",
            f"Profile: {summary or 'first profile seeded'}",
            f"Facts: {len(result.get('profile_facts') or [])}",
            f"Browser entries: {browser.get('entries_count', 0)}",
        ],
    )

    topics = result.get("topics") or []
    if topics:
        ui.table(
            "Topics",
            ["topic", "source", "confidence"],
            [
                (
                    item.get("topic", ""),
                    item.get("source_type", ""),
                    item.get("confidence", ""),
                )
                for item in topics[:8]
            ],
        )

    suggestions = result.get("suggestions") or []
    if suggestions:
        ui.table(
            "Suggestions",
            ["suggestion", "source"],
            [
                (
                    item.get("statement", ""),
                    item.get("source_type", ""),
                )
                for item in suggestions[:5]
            ],
        )

    next_actions = result.get("next_actions") or []
    if next_actions:
        ui.panel("Next Actions", [f"- {action}" for action in next_actions])


def _print_backend_status(status: dict, ui: CLIUI) -> None:
    ui.panel(
        "Kith Backend",
        [
            f"Name: {status.get('name', 'AgentOS')} v{status.get('version', '?')}",
            f"PID: {status.get('pid', '?')}",
            f"Running: {status.get('running', False)}",
            f"Subsystems: {', '.join(status.get('subsystems', []))}",
        ],
    )

    fs = status.get("filesystem") or {}
    if fs:
        ui.table(
            "Filesystem",
            ["indexed", "scan", "progress", "watcher"],
            [[
                fs.get("files_indexed", 0),
                "running" if fs.get("scan_in_progress") else "idle",
                fs.get("scan_progress", fs.get("scan_progress_files", 0)),
                "on" if fs.get("realtime_watcher") or fs.get("realtime") else "off",
            ]],
        )

    memory = status.get("memory") or {}
    if memory:
        ui.table(
            "Memory",
            ["files", "summaries", "knowledge", "chunks", "cache"],
            [[
                memory.get("indexed_files", 0),
                memory.get("summarized_files", 0),
                memory.get("knowledge_entries", 0),
                memory.get("document_chunks", 0),
                memory.get("cache_items", 0),
            ]],
        )

    llm = status.get("llm") or {}
    rag = status.get("rag") or {}
    scheduler = status.get("scheduler") or {}
    cron = status.get("cron") or {}
    insight = status.get("first_insight") or {}
    ui.table(
        "Runtime",
        ["llm", "scheduler", "cron", "rag", "first_insight"],
        [[
            ", ".join(llm.get("available_providers") or []) or "none",
            f"{scheduler.get('active_tasks', 0)} active / {scheduler.get('queue_size', 0)} queued",
            cron.get("strategy", "unknown") if cron else "unknown",
            f"{rag.get('chunks', 0)} chunks, {rag.get('pending', 0)} pending" if rag else "unknown",
            "ready" if insight.get("ready") else "pending",
        ]],
    )


def cmd_search(args: argparse.Namespace) -> None:
    asyncio.run(_async_search(args.query, args.type))


async def _async_search(query: str, file_type: str | None) -> None:
    from src.syscall.client import SysAgentClient
    try:
        async with SysAgentClient() as client:
            results = await client.file_search(query, file_type=file_type)
            if not results:
                print("No matches found.")
                return
            for r in results:
                print(f"  {r.get('file_type', '?'):6s}  {r.get('path', '?')}")
                if r.get("summary"):
                    print(f"         {r['summary'][:80]}...")
                print()
    except (ConnectionRefusedError, FileNotFoundError):
        print("SysAgent is not running.")


def cmd_query(args: argparse.Namespace) -> None:
    asyncio.run(_async_query(args.category))


async def _async_query(category: str | None) -> None:
    from src.syscall.client import SysAgentClient
    try:
        async with SysAgentClient() as client:
            entries = await client.knowledge_query(category=category)
            if not entries:
                print("No knowledge entries found.")
                return
            print(f"\n{'='*60}")
            print(f" Knowledge entries: {len(entries)}" + (f" (category: {category})" if category else ""))
            print(f"{'='*60}")
            for e in entries:
                entry_id = e.get("id", "?")
                cat = e.get("category", "?")
                content_raw = e.get("content", "")
                print(f"\n--- [{cat}] {entry_id} ---")
                try:
                    parsed = json.loads(content_raw)
                    print(json.dumps(parsed, indent=2, ensure_ascii=False))
                except (json.JSONDecodeError, TypeError):
                    print(content_raw)
            print()
    except (ConnectionRefusedError, FileNotFoundError):
        print("SysAgent is not running.")


def cmd_report(args: argparse.Namespace) -> None:
    asyncio.run(_async_report(args.type, getattr(args, "project_dir", None)))


async def _async_report(report_type: str, project_dir: str | None) -> None:
    from src.syscall.client import SysAgentClient
    try:
        async with SysAgentClient() as client:
            if report_type == "daily":
                data = await client.report_daily()
            elif report_type == "project":
                data = await client.report_project(project_dir)
            elif report_type == "brief":
                data = await client.report_brief()
            else:
                print(f"Unknown report type: {report_type}")
                return
            _print_full_json(f"Report [{report_type}]", data)
    except (ConnectionRefusedError, FileNotFoundError):
        print("SysAgent is not running.")


def cmd_profile(args: argparse.Namespace) -> None:
    asyncio.run(_async_profile())


async def _async_profile() -> None:
    from src.syscall.client import SysAgentClient
    try:
        async with SysAgentClient() as client:
            data = await client.profile_get()
            _print_full_json("User Profile", data)
    except (ConnectionRefusedError, FileNotFoundError):
        print("SysAgent is not running.")


def cmd_summarize(args: argparse.Namespace) -> None:
    asyncio.run(_async_summarize(args.batch_size))


async def _async_summarize(batch_size: int) -> None:
    from src.syscall.client import SysAgentClient
    try:
        async with SysAgentClient() as client:
            data = await client.summarize_files(batch_size)
            _print_summarize_result(data)
    except (ConnectionRefusedError, FileNotFoundError):
        print("SysAgent is not running.")


def _print_full_json(title: str, data: dict) -> None:
    """Pretty-print full JSON output with a header, ensuring no truncation."""
    print(f"\n{'='*60}")
    print(f" {title}")
    print(f"{'='*60}")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print()


def _print_summarize_result(data: dict) -> None:
    mode = data.get("mode", "?")
    total = data.get("summarized", 0)
    vision = data.get("vision_files", 0)
    docs = data.get("document_files", 0)
    code = total - vision - docs
    errors = data.get("errors", 0)
    elapsed = data.get("elapsed_seconds", 0)
    candidates = data.get("total_candidates", total)

    print(f"\n{'='*60}")
    print(f" Summarizer [{mode}]  —  {total}/{candidates} files in {elapsed:.1f}s")
    print(f"{'='*60}")
    print(f"  Code: {code}   Documents: {docs}   Images: {vision}   Errors: {errors}")

    files_list = data.get("files", [])
    if files_list:
        print(f"\n  {'Type':<10} {'Path':<50} Summary")
        print(f"  {'-'*10} {'-'*50} {'-'*40}")
        for f in files_list:
            ftype = f.get("type", "?")
            path = f.get("path", "")
            short = path.replace(str(Path.home()), "~")
            if len(short) > 48:
                short = "..." + short[-45:]
            summary = f.get("summary", "")[:60]
            print(f"  {ftype:<10} {short:<50} {summary}")
    print()


def cmd_analyze(args: argparse.Namespace) -> None:
    asyncio.run(_async_analyze(args.hours))


async def _async_analyze(hours: float) -> None:
    from src.syscall.client import SysAgentClient
    try:
        async with SysAgentClient() as client:
            data = await client.analyze_behavior(hours)
            _print_full_json("Behavior Analysis", data)
    except (ConnectionRefusedError, FileNotFoundError):
        print("SysAgent is not running.")


def cmd_triage(args: argparse.Namespace) -> None:
    asyncio.run(_async_triage(args.batch_size))


async def _async_triage(batch_size: int) -> None:
    from src.syscall.client import SysAgentClient
    try:
        async with SysAgentClient() as client:
            print("Running file importance triage (LLM decides what's worth summarizing)...")
            data = await client.triage_files(batch_size)
            _print_triage_result(data)
    except (ConnectionRefusedError, FileNotFoundError):
        print("SysAgent is not running. Start with: agent-sys start")


def _print_triage_result(data: dict) -> None:
    print(f"\n{'='*60}")
    print(f" File Triage Results")
    print(f"{'='*60}")

    rule_skipped = data.get("rule_based_skipped", 0)
    llm_triaged = data.get("llm_triaged", 0)
    elapsed = data.get("elapsed_seconds", 0)
    dist = data.get("triage_distribution", {})

    if rule_skipped:
        print(f"  Rule-based skip:  {rule_skipped:>8} files (obvious noise)")
    if llm_triaged:
        print(f"  LLM classified:   {llm_triaged:>8} files")
    print(f"  Elapsed:          {elapsed:.1f}s")
    print()

    if dist:
        print("  Current distribution:")
        for status, count in sorted(dist.items(), key=lambda x: -x[1]):
            bar_len = min(int(count / max(dist.values()) * 30), 30)
            bar = "#" * bar_len
            print(f"    {status:<12} {count:>8}  {bar}")
    print()


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Launch the web dashboard for visual debugging."""
    try:
        from src.web.dashboard import run_dashboard
        run_dashboard(port=args.port)
    except ImportError as e:
        print(f"Error: {e}")
        print("Install aiohttp to use the dashboard: pip install aiohttp")


def cmd_classify(args: argparse.Namespace) -> None:
    asyncio.run(_async_classify())


async def _async_classify() -> None:
    from src.syscall.client import SysAgentClient
    try:
        async with SysAgentClient() as client:
            data = await client.classify_priority()
            print(json.dumps(data, indent=2, ensure_ascii=False))
    except (ConnectionRefusedError, FileNotFoundError):
        print("SysAgent is not running.")


_LLM_CONFIG_PATH = Path.home() / ".agent_sys" / "llm_config.yaml"
_SCAN_CONFIG_PATH = Path.home() / ".agent_sys" / "scan_config.yaml"
_FIRSTRUN_MARKER = Path.home() / ".agent_sys" / ".firstrun_done"


def _load_saved_scan_config(config) -> object:
    """Apply any persisted watch_paths override from the first-run wizard."""
    try:
        from src.kernel.user_settings import load_scan_settings
        saved = load_scan_settings([str(p) for p in config.filesystem.watch_paths])
        paths = saved.get("watch_paths") or []
        if paths:
            config.filesystem.watch_paths = [
                Path(os.path.expanduser(p)).expanduser().resolve()
                for p in paths
            ]
    except Exception as e:
        logging.getLogger("agent_sys.cli").warning(
            "Failed to load saved scan config: %s", e,
        )
    return config


def _save_scan_config(watch_paths: list[str]) -> None:
    from src.kernel.user_settings import save_scan_settings
    save_scan_settings(watch_paths)


def _prompt_scan_paths(config) -> object:
    """First-run interactive prompt: which directories should agent-sys index?

    Skipped if the user has already answered (marker file exists) or if
    input is not a TTY (daemonized / scripted start).
    """
    if _FIRSTRUN_MARKER.exists():
        return config
    if not sys.stdin.isatty():
        return config

    home = str(Path.home())
    default_paths = [p for p in ["~/Documents", "~/Desktop"]]

    print(
        "\nAgentOS will index files under the paths you choose and may send\n"
        "their contents to your configured LLM provider. Pick a scope:\n"
    )
    print("     [1] Conservative: ~/Documents + ~/Desktop  (recommended)")
    print("     [2] Projects:     ~/Documents + ~/Desktop + ~/Projects")
    print("     [3] Everything:   ~/  (entire home directory — more data, more bandwidth)")
    print("     [4] Custom:       enter paths manually")
    print("     [5] Skip:         don't change config (use default.yaml)\n")

    choice = ""
    while choice not in ("1", "2", "3", "4", "5"):
        choice = input("   Enter choice [1-5]: ").strip() or "1"

    if choice == "1":
        paths = default_paths
    elif choice == "2":
        paths = default_paths + ["~/Projects"]
    elif choice == "3":
        print(
            "\n   ⚠  Indexing ~/ can include sensitive files (tax returns, SSH keys, etc.)\n"
            "      even with the default ignore patterns. Continuing in 3s —\n"
            "      Ctrl-C to abort.\n"
        )
        import time as _t
        _t.sleep(3)
        paths = ["~"]
    elif choice == "4":
        print("   Enter one path per line. Empty line to finish:")
        paths = []
        while True:
            p = input("     path> ").strip()
            if not p:
                break
            paths.append(p)
        if not paths:
            paths = default_paths
    else:
        _FIRSTRUN_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _FIRSTRUN_MARKER.touch()
        return config

    _save_scan_config(paths)
    config.filesystem.watch_paths = [
        Path(os.path.expanduser(p)).expanduser().resolve()
        for p in paths
    ]
    _FIRSTRUN_MARKER.parent.mkdir(parents=True, exist_ok=True)
    _FIRSTRUN_MARKER.touch()
    print(f"\n   ✓ Will index: {', '.join(paths)}")
    print(f"   ✓ Saved to {_SCAN_CONFIG_PATH}\n")
    return config


def _detect_ollama_models(base_url: str = "http://localhost:11434") -> list[str]:
    """Query a local Ollama instance for the list of installed models.
    Returns [] if Ollama isn't reachable."""
    try:
        import urllib.request
        import json as _json
        req = urllib.request.Request(f"{base_url}/api/tags")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = _json.loads(resp.read())
        return [m.get("name") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def _load_saved_llm_config(config) -> object:
    """Load persisted LLM config from ~/.agent_sys/llm_config.yaml if it exists."""
    import yaml

    if not _LLM_CONFIG_PATH.exists():
        return config

    try:
        with open(_LLM_CONFIG_PATH) as f:
            saved = yaml.safe_load(f) or {}

        if saved.get("mode") == "local":
            config.llm.default_provider = ""
            config.llm.providers = {}
        elif saved.get("default_provider"):
            config.llm.default_provider = saved["default_provider"]
        if saved.get("providers"):
            config.llm.providers.update(saved["providers"])

        for env_var, value in saved.get("env_vars", {}).items():
            os.environ.setdefault(env_var, value)

        logging.getLogger("agent_sys.cli").info(
            "Loaded saved LLM config: provider=%s", saved.get("default_provider")
        )
    except Exception as e:
        logging.getLogger("agent_sys.cli").warning("Failed to load saved LLM config: %s", e)

    return config


def _save_llm_config(config, env_vars: dict[str, str] | None = None) -> None:
    """Persist LLM provider config so it survives restarts."""
    import yaml

    _LLM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "default_provider": config.llm.default_provider,
        "providers": config.llm.providers,
        "env_vars": env_vars or {},
    }

    with open(_LLM_CONFIG_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False)

    os.chmod(_LLM_CONFIG_PATH, 0o600)


def _check_llm_and_prompt(config) -> object:
    """
    Pre-boot LLM check.  First try to load saved config from disk.
    If still no provider available, interactively prompt the user.
    Returns the (possibly mutated) config.
    """
    from src.llm.router import check_llm_availability

    config = _load_saved_llm_config(config)

    llm_cfg = {
        "default_provider": config.llm.default_provider,
        "providers": config.llm.providers,
        "routing": config.llm.routing,
    }
    available = check_llm_availability(llm_cfg)
    if available:
        return config

    print(
        "\n⚠  No LLM provider detected (no API keys set).\n"
        "   Smart agents (summarize, analyze, report…) need an LLM backend.\n"
    )

    # Proactively detect a running local Ollama and mention it in the prompt.
    local_models = _detect_ollama_models()
    if local_models:
        print(f"   ✓ Detected running Ollama with {len(local_models)} model(s) installed\n")

    print("   Choose how to proceed:\n")
    ollama_hint = f" ({len(local_models)} models available)" if local_models else ""
    print(f"     [1] Local Ollama{ollama_hint}")
    print("                          (recommended: no API key, stays on your machine)")
    print("     [2] Remote API     — enter an OpenAI or Anthropic API key")
    print("     [3] Skip           — continue without LLM (rule-based mode only)\n")

    default_choice = "1" if local_models else "2"
    choice = ""
    while choice not in ("1", "2", "3"):
        choice = input(f"   Enter choice [1/2/3, default={default_choice}]: ").strip() or default_choice

    if choice == "1":
        config = _setup_ollama(config)
    elif choice == "2":
        config = _setup_api_key(config)
    else:
        print("\n   Continuing without LLM — smart agents will fall back to rule-based mode.\n")

    return config


def _setup_ollama(config) -> object:
    """Configure the openai_compatible adapter to point at a local Ollama instance.

    Tries to auto-detect a running Ollama at localhost:11434 and lists the
    locally installed models so the user can pick one directly.
    """
    default_host = "http://localhost:11434"
    default_url = f"{default_host}/v1"

    installed = _detect_ollama_models(default_host)
    if installed:
        print(f"\n   Detected running Ollama at {default_host}. Installed models:")
        for i, name in enumerate(installed, 1):
            print(f"     [{i}] {name}")
        print("     [c] Custom model name\n")
        choice = input(f"   Pick a model [1-{len(installed)}/c, default=1]: ").strip() or "1"
        if choice.isdigit() and 1 <= int(choice) <= len(installed):
            model = installed[int(choice) - 1]
        else:
            model = input("   Model name: ").strip() or installed[0]
        url = default_url
    else:
        print(
            "\n   ⚠  Couldn't reach Ollama at localhost:11434. Is `ollama serve` running?\n"
            "      You can still configure it manually — we'll save the config for later.\n"
        )
        url = input(f"   Ollama base URL [{default_url}]: ").strip() or default_url
        print("\n   Common Ollama models: llama3, llama3.1, mistral, gemma2, qwen2.5")
        model = input("   Model name [llama3.1]: ").strip() or "llama3.1"

    config.llm.default_provider = "openai_compatible"
    config.llm.providers["openai_compatible"] = {
        "base_url": url,
        "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
        "models": {"fast": model, "strong": model},
    }
    os.environ.setdefault("OPENAI_COMPATIBLE_API_KEY", "ollama")

    _save_llm_config(config, env_vars={"OPENAI_COMPATIBLE_API_KEY": "ollama"})

    print(f"\n   ✓ Configured Ollama at {url} with model '{model}'")
    print("   ✓ Config saved to ~/.agent_sys/llm_config.yaml (will persist across restarts)\n")
    return config


def _setup_api_key(config) -> object:
    """Prompt for an API key, set it in the environment, and persist to disk."""
    print("\n   Which provider?\n")
    print("     [1] OpenAI              (GPT-4o / GPT-4o-mini)")
    print("     [2] OpenAI Compatible   (DeepSeek, Groq, any OpenAI-format API)")
    print("     [3] Anthropic           (Claude Sonnet / Opus)")
    print("     [4] Anthropic Compatible (MiniMax, custom Anthropic API endpoint)\n")

    provider = ""
    while provider not in ("1", "2", "3", "4"):
        provider = input("   Enter choice [1/2/3/4]: ").strip()

    env_vars: dict[str, str] = {}

    if provider == "1":
        key = input("   OPENAI_API_KEY: ").strip()
        if key:
            os.environ["OPENAI_API_KEY"] = key
            config.llm.default_provider = "openai"
            env_vars["OPENAI_API_KEY"] = key
            _save_llm_config(config, env_vars=env_vars)
            print("\n   ✓ OpenAI API key configured and saved to ~/.agent_sys/llm_config.yaml\n")
    elif provider == "2":
        base_url = input("   Base URL (e.g. https://api.deepseek.com/v1): ").strip()
        key = input("   OPENAI_COMPATIBLE_API_KEY: ").strip()
        model = input("   Model name [deepseek-chat]: ").strip() or "deepseek-chat"
        if base_url:
            os.environ["OPENAI_COMPATIBLE_API_KEY"] = key or "none"
            config.llm.default_provider = "openai_compatible"
            config.llm.providers["openai_compatible"] = {
                "base_url": base_url,
                "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
                "models": {"fast": model, "strong": model},
            }
            env_vars["OPENAI_COMPATIBLE_API_KEY"] = key or "none"
            _save_llm_config(config, env_vars=env_vars)
            print(f"\n   ✓ Configured OpenAI Compatible at {base_url} with model '{model}'.")
            print("   ✓ Config saved to ~/.agent_sys/llm_config.yaml\n")
    elif provider == "3":
        key = input("   ANTHROPIC_API_KEY: ").strip()
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
            config.llm.default_provider = "anthropic"
            env_vars["ANTHROPIC_API_KEY"] = key
            _save_llm_config(config, env_vars=env_vars)
            print("\n   ✓ Anthropic API key configured and saved to ~/.agent_sys/llm_config.yaml\n")
    else:
        default_url = "https://api.minimaxi.com/anthropic"
        base_url = input(f"   Anthropic-compatible Base URL [{default_url}]: ").strip() or default_url
        key = input("   ANTHROPIC_COMPATIBLE_API_KEY: ").strip()
        model = input("   Model name [MiniMax-M2.7]: ").strip() or "MiniMax-M2.7"
        if key:
            os.environ["ANTHROPIC_COMPATIBLE_API_KEY"] = key
            config.llm.default_provider = "anthropic_compatible"
            config.llm.providers["anthropic_compatible"] = {
                "base_url": base_url,
                "api_key_env": "ANTHROPIC_COMPATIBLE_API_KEY",
                "models": {"fast": model, "strong": model},
            }
            env_vars["ANTHROPIC_COMPATIBLE_API_KEY"] = key
            _save_llm_config(config, env_vars=env_vars)
            print(f"\n   ✓ Configured Anthropic Compatible at {base_url} with model '{model}'.")
            print("   ✓ Config saved to ~/.agent_sys/llm_config.yaml\n")

    return config


def _daemonize() -> None:
    """Classic Unix double-fork daemonize."""
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    sys.stdin = open(os.devnull)
    sys.stdout = open(os.devnull, "w")
    sys.stderr = open(os.devnull, "w")


_EPILOG = """\
command groups:
  command center:    home, doctor, statusline
  daemon lifecycle:  start, stop, status, ping, logs
  onboarding:        first-insight
  query/browse:      search, query, profile, report
  manual runs:       triage, summarize, analyze, classify
  ui:                dashboard

examples:
  agent-sys                        # open the Kith command center
  agent-sys doctor                 # check environment and daemon health
  agent-sys start                  # start the daemon (first run will prompt)
  agent-sys start -d               # detach into background
  agent-sys logs -f                # follow daemon logs
  agent-sys first-insight          # seed the first correctable profile
  agent-sys search "redis config"  # search indexed files semantically
  agent-sys report daily           # generate today's report
  agent-sys dashboard              # launch the web UI
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agent-sys",
        description=(
            "agent-sys — a long-running local agent daemon that indexes your "
            "files and exposes an LLM-powered RPC surface to other agents."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", "-c", help="Path to config YAML file")
    sub = parser.add_subparsers(
        dest="command",
        metavar="{home,doctor,statusline,start,stop,status,ping,search,query,"
                "profile,report,first-insight,triage,summarize,analyze,"
                "classify,dashboard,logs}",
    )

    # ── Command center ──
    p_home = sub.add_parser("home", aliases=["menu"], help="Show the Kith command center")
    p_home.set_defaults(func=cmd_home)

    p_doctor = sub.add_parser("doctor", help="Check local Kith backend health")
    p_doctor.set_defaults(func=cmd_doctor)

    p_statusline = sub.add_parser("statusline", help="Print compact one-line backend status")
    p_statusline.set_defaults(func=cmd_statusline)

    # ── Daemon lifecycle ──
    p_start = sub.add_parser(
        "start",
        help="Start the daemon (interactive on first run)",
        description=(
            "Start the agent-sys daemon. On first run you'll be asked which "
            "directories to index and which LLM provider to use; later runs "
            "reuse ~/.agent_sys/{scan,llm}_config.yaml without prompting."
        ),
    )
    p_start.add_argument("-d", "--daemon", action="store_true", help="Run as background daemon")
    p_start.add_argument(
        "-f", "--force", action="store_true",
        help="Terminate any existing agent-sys instance before starting",
    )
    p_start.add_argument(
        "--verbose",
        action="store_true",
        help="Also stream daemon logs to stderr during foreground startup",
    )
    first_insight_group = p_start.add_mutually_exclusive_group()
    first_insight_group.add_argument(
        "--first-insight",
        dest="first_insight",
        action="store_true",
        default=None,
        help="Run the interactive First Insight flow after the daemon is online",
    )
    first_insight_group.add_argument(
        "--no-first-insight",
        dest="first_insight",
        action="store_false",
        help="Skip the First Insight prompt for this start",
    )
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop the running daemon")
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="Show kernel/scheduler/memory status")
    p_status.add_argument("--json", action="store_true", help="Print raw JSON status")
    p_status.set_defaults(func=cmd_status)

    p_ping = sub.add_parser("ping", help="Check if the daemon is alive")
    p_ping.set_defaults(func=cmd_ping)

    p_logs = sub.add_parser("logs", aliases=["log"], help="Show daemon logs")
    p_logs.add_argument("-n", "--lines", type=int, default=120, help="Number of recent lines to show")
    p_logs.add_argument("-f", "--follow", action="store_true", help="Follow logs in real time")
    p_logs.set_defaults(func=cmd_logs)

    # ── Onboarding ──
    p_first = sub.add_parser(
        "first-insight",
        help="Run backend First Insight onboarding from the CLI",
        description=(
            "Seed Kith's first correctable user profile from lightweight answers, "
            "the local file index, and optional browser metadata aggregation."
        ),
    )
    p_first.add_argument("--role", "--roles", dest="roles", action="append", default=[], help="Role(s), comma-separated or repeatable")
    p_first.add_argument("--goal", "--goals", dest="goals", action="append", default=[], help="Goal(s), comma-separated or repeatable")
    p_first.add_argument("--interest", "--interests", dest="interests", action="append", default=[], help="Interest keyword(s)")
    p_first.add_argument("--focus", dest="current_focus", action="append", default=[], help="Current focus item(s)")
    p_first.add_argument("--planning-style", default="lightweight", help="Planning style hint")
    p_first.add_argument(
        "--cadence",
        dest="suggestion_cadence",
        choices=["daily", "weekly", "quiet"],
        default="daily",
        help="Suggestion cadence",
    )
    browser_group = p_first.add_mutually_exclusive_group()
    browser_group.add_argument(
        "--browser-history",
        dest="include_browser_history",
        action="store_true",
        default=None,
        help="Allow aggregated Chromium-family titles/domains/download metadata",
    )
    browser_group.add_argument(
        "--no-browser-history",
        dest="include_browser_history",
        action="store_false",
        help="Do not read browser metadata",
    )
    p_first.add_argument("--history-days", type=int, default=30, help="Browser history lookback days")
    p_first.add_argument("--history-limit", type=int, default=500, help="Browser metadata row limit")
    p_first.add_argument("--yes", action="store_true", help="Do not prompt for missing answers")
    p_first.add_argument("--json", action="store_true", help="Print raw First Insight JSON")
    p_first.set_defaults(func=cmd_first_insight)

    # ── Query / browse ──
    p_search = sub.add_parser(
        "search", help="Search indexed files (vector + SQL fallback)",
    )
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--type", "-t", help="Filter by file extension (e.g. .py)")
    p_search.set_defaults(func=cmd_search)

    p_query = sub.add_parser("query", help="List entries in the knowledge base")
    p_query.add_argument("--category", "-cat", help="Filter by category")
    p_query.set_defaults(func=cmd_query)

    p_profile = sub.add_parser("profile", help="Show current user profile")
    p_profile.set_defaults(func=cmd_profile)

    p_report = sub.add_parser("report", help="Generate or fetch a report")
    p_report.add_argument("type", choices=["daily", "project", "brief"], help="Report type")
    p_report.add_argument("--project-dir", help="Project directory (for project report)")
    p_report.set_defaults(func=cmd_report)

    # ── Manual agent runs ──
    p_triage = sub.add_parser("triage", help="Run file importance triage manually")
    p_triage.add_argument("--batch-size", type=int, default=500, help="Files per triage batch")
    p_triage.set_defaults(func=cmd_triage)

    p_summarize = sub.add_parser("summarize", help="Run LLM summarization on unsummarized files")
    p_summarize.add_argument("--batch-size", type=int, default=30, help="Number of files per batch")
    p_summarize.set_defaults(func=cmd_summarize)

    p_analyze = sub.add_parser("analyze", help="Run behavior analysis manually")
    p_analyze.add_argument("--hours", type=float, default=168, help="Hours of history to analyze")
    p_analyze.set_defaults(func=cmd_analyze)

    p_classify = sub.add_parser("classify", help="Recompute file priority (hot/warm/cold)")
    p_classify.set_defaults(func=cmd_classify)

    # ── UI ──
    p_dashboard = sub.add_parser("dashboard", help="Launch the web dashboard")
    p_dashboard.add_argument(
        "--port", "-p", type=int, default=7438, help="Dashboard port (default 7438)",
    )
    p_dashboard.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    if not args.command:
        args.func = cmd_home

    args.func(args)


if __name__ == "__main__":
    main()
