"""
AgentOS — SysAgent System

A long-running agent daemon that indexes the user's files and exposes
an RPC surface (Unix socket + HTTP) so external agents can query the
resulting knowledge base.

The code uses an OS-flavoured vocabulary (Kernel, Scheduler, Syscall,
Cron, Memory) as a design narrative, but under the hood these are
ordinary building blocks: a task queue with concurrency limits, an
RPC server, an LLM-driven policy engine, and a SQLite-backed store
with an LRU cache. Don't read the metaphor as a hard architectural
constraint.
"""

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    try:
        __version__ = _pkg_version("agent-sys")
    except PackageNotFoundError:
        __version__ = "0.7.0"
except ImportError:
    __version__ = "0.7.0"
