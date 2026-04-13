"""
AgentOS — SysAgent System

Maps traditional operating system concepts to an LLM-Agent architecture:
  Kernel    → SysAgent daemon (persistent background process)
  Thread    → Sub-agent workers (dispatched for specific tasks)
  FileSystem → File watcher + knowledge index
  Scheduler → Agent thread pool with priority queue
  Syscall   → API layer for external agents (Cursor, Claude Code, etc.)
  Memory    → Context cache + vector store for persistent knowledge
"""

__version__ = "0.2.0"
