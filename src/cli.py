"""
CLI Entry Point — the 'shell' of AgentOS.

Commands:
    agent-sys start      Start the SysAgent daemon (foreground)
    agent-sys start -d   Start as background daemon
    agent-sys stop       Stop the running daemon
    agent-sys status     Show system status
    agent-sys search     Search indexed files
    agent-sys query      Query knowledge base
    agent-sys ping       Check if daemon is alive
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path


def setup_logging(level: str = "INFO") -> None:
    log_dir = Path.home() / ".agent_sys" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "sysagent.log"),
        ],
    )


def cmd_start(args: argparse.Namespace) -> None:
    """Start the SysAgent kernel."""
    from src.kernel.config import load_config
    from src.kernel.daemon import SysAgentKernel

    config = load_config(args.config)
    setup_logging(config.kernel.log_level)
    logger = logging.getLogger("agent_sys.cli")

    pid_file = Path(str(config.kernel.pid_file))
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)
            # Process exists — but could be a zombie or suspended process
            # Check if it's actually our agent-sys or a recycled PID
            if old_pid == os.getpid():
                pid_file.unlink(missing_ok=True)
            else:
                logger.warning("Found existing process PID %d, attempting cleanup...", old_pid)
                os.kill(old_pid, signal.SIGTERM)
                import time as _time
                _time.sleep(1)
                try:
                    os.kill(old_pid, 0)
                    # Still alive — force kill
                    os.kill(old_pid, signal.SIGKILL)
                    _time.sleep(0.5)
                except ProcessLookupError:
                    pass
                pid_file.unlink(missing_ok=True)
                logger.info("Cleaned up old process, starting fresh.")
        except (ProcessLookupError, ValueError):
            pid_file.unlink(missing_ok=True)

    # Check LLM availability before boot — prompt user if none found
    if not args.daemon:
        config = _check_llm_and_prompt(config)

    if args.daemon:
        _daemonize()

    kernel = SysAgentKernel(config)

    print(f"""
╔══════════════════════════════════════════╗
║          AgentOS — SysAgent v{config.kernel.version}        ║
║                                          ║
║  Kernel:     PID {os.getpid():<24}║
║  Socket:     {str(config.kernel.socket_path):<27}║
║  HTTP:       http://127.0.0.1:{config.syscall.http_port:<11}║
║  Log:        ~/.agent_sys/logs/          ║
║                                          ║
║  Traditional OS → Agent OS mapping:      ║
║    CPU Thread  → Agent Worker            ║
║    FileSystem  → Knowledge Index         ║
║    Scheduler   → Task Dispatcher         ║
║    Syscall     → Agent API               ║
║    Memory      → Context Cache           ║
╚══════════════════════════════════════════╝
    """)

    try:
        asyncio.run(kernel.run())
    except KeyboardInterrupt:
        print("\nShutdown requested.")


def cmd_stop(args: argparse.Namespace) -> None:
    """Stop the running SysAgent daemon."""
    pid_file = Path("/tmp/agent_sys.pid")
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
    asyncio.run(_async_status())


async def _async_status() -> None:
    from src.syscall.client import SysAgentClient
    try:
        async with SysAgentClient() as client:
            status = await client.status()
            print(json.dumps(status, indent=2))
    except (ConnectionRefusedError, FileNotFoundError):
        print("SysAgent is not running. Start with: agent-sys start")


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
                kid = e.get("id", "?")
                cat = e.get("category", "?")
                content_raw = e.get("content", "")
                print(f"\n--- [{cat}] {kid} ---")
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


def _load_saved_llm_config(config) -> object:
    """Load persisted LLM config from ~/.agent_sys/llm_config.yaml if it exists."""
    import yaml

    if not _LLM_CONFIG_PATH.exists():
        return config

    try:
        with open(_LLM_CONFIG_PATH) as f:
            saved = yaml.safe_load(f) or {}

        if saved.get("default_provider"):
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
    print("   Choose how to proceed:\n")
    print("     [1] Local Ollama   — use a model running on your machine")
    print("                          (requires Ollama to be running at localhost:11434)")
    print("     [2] Remote API     — enter an OpenAI or Anthropic API key")
    print("     [3] Skip           — continue without LLM (rule-based mode only)\n")

    choice = ""
    while choice not in ("1", "2", "3"):
        choice = input("   Enter choice [1/2/3]: ").strip()

    if choice == "1":
        config = _setup_ollama(config)
    elif choice == "2":
        config = _setup_api_key(config)
    else:
        print("\n   Continuing without LLM — smart agents will fall back to rule-based mode.\n")

    return config


def _setup_ollama(config) -> object:
    """Configure the openai_compatible adapter to point at a local Ollama instance."""
    default_url = "http://localhost:11434/v1"
    url = input(f"   Ollama base URL [{default_url}]: ").strip() or default_url

    print("\n   Common Ollama models: llama3, mistral, gemma2, phi3, qwen2")
    model = input("   Model name [llama3]: ").strip() or "llama3"

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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agent-sys",
        description="AgentOS — System-level Agent daemon that maps OS concepts to LLM agents",
    )
    parser.add_argument("--config", "-c", help="Path to config YAML file")
    sub = parser.add_subparsers(dest="command")

    # start
    p_start = sub.add_parser("start", help="Start the SysAgent daemon")
    p_start.add_argument("-d", "--daemon", action="store_true", help="Run as background daemon")
    p_start.set_defaults(func=cmd_start)

    # stop
    p_stop = sub.add_parser("stop", help="Stop the SysAgent daemon")
    p_stop.set_defaults(func=cmd_stop)

    # status
    p_status = sub.add_parser("status", help="Show system status")
    p_status.set_defaults(func=cmd_status)

    # ping
    p_ping = sub.add_parser("ping", help="Ping the daemon")
    p_ping.set_defaults(func=cmd_ping)

    # search
    p_search = sub.add_parser("search", help="Search indexed files")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--type", "-t", help="Filter by file extension (e.g. .py)")
    p_search.set_defaults(func=cmd_search)

    # query
    p_query = sub.add_parser("query", help="Query knowledge base")
    p_query.add_argument("--category", "-cat", help="Filter by category")
    p_query.set_defaults(func=cmd_query)

    # v0.2 smart agent commands
    p_report = sub.add_parser("report", help="Generate or fetch a report")
    p_report.add_argument("type", choices=["daily", "project", "brief"], help="Report type")
    p_report.add_argument("--project-dir", help="Project directory (for project report)")
    p_report.set_defaults(func=cmd_report)

    p_profile = sub.add_parser("profile", help="Show user profile")
    p_profile.set_defaults(func=cmd_profile)

    p_summarize = sub.add_parser("summarize", help="Run LLM summarization on unsummarized files")
    p_summarize.add_argument("--batch-size", type=int, default=30, help="Number of files per batch")
    p_summarize.set_defaults(func=cmd_summarize)

    p_analyze = sub.add_parser("analyze", help="Run behavior analysis")
    p_analyze.add_argument("--hours", type=float, default=168, help="Hours of history to analyze")
    p_analyze.set_defaults(func=cmd_analyze)

    p_classify = sub.add_parser("classify", help="Run file priority classification")
    p_classify.set_defaults(func=cmd_classify)

    p_triage = sub.add_parser("triage", help="Run LLM-based file importance triage")
    p_triage.add_argument("--batch-size", type=int, default=500, help="Files per triage batch")
    p_triage.set_defaults(func=cmd_triage)

    p_dashboard = sub.add_parser("dashboard", help="Launch web dashboard for visual debugging")
    p_dashboard.add_argument("--port", "-p", type=int, default=7438, help="Dashboard port (default 7438)")
    p_dashboard.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
