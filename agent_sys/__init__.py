"""
Public import alias for the agent-sys source tree.

The source tree lives under `src/` for historical reasons, but `src` is not
a useful outward-facing name. External agents should prefer:

    from agent_sys.syscall.client import SysAgentClient
    from agent_sys.agents.base   import BaseAgent, AgentTask

This module is a thin shim: it reuses `src`'s package path, so
`agent_sys.syscall.client` resolves to exactly the same file as
`src.syscall.client`. We intentionally do NOT `sys.modules`-alias the two,
because that can create subtle singleton issues when both names are used
concurrently. Pick one style per codebase.
"""

from __future__ import annotations

import importlib

_src = importlib.import_module("src")

__path__ = list(_src.__path__)
__version__ = getattr(_src, "__version__", "0.0.0")
