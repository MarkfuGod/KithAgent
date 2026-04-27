"""
Syscall Protocol — the 'ABI' between external agents and AgentOS.

Defines the message format for all inter-agent communication.
Think of this as the syscall number table in Linux — each operation
has a defined name and expected parameters.

Transport-agnostic: works over Unix socket, TCP, or HTTP.
"""

from __future__ import annotations

import json
import uuid
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class SyscallType(str, Enum):
    # File operations
    FILE_SEARCH = "file.search"
    FILE_READ = "file.read"
    FILE_LIST = "file.list"
    FILE_SUMMARIZE = "summarize.file"

    # Knowledge operations
    KNOWLEDGE_QUERY = "knowledge.query"
    KNOWLEDGE_STORE = "knowledge.store"

    # Context / session management
    CONTEXT_SAVE = "context.save"
    CONTEXT_LOAD = "context.load"

    # Agent management
    AGENT_SUBMIT = "agent.submit"
    AGENT_STATUS = "agent.task_status"

    # Reports (v0.2)
    REPORT_DAILY = "report.daily"
    REPORT_PROJECT = "report.project"
    REPORT_BRIEF = "report.brief"

    # Profile (v0.2)
    PROFILE_GET = "profile.get"
    PROFILE_SUMMARY = "profile.summary"

    # Consumer assistant surface (desktop app)
    ASSISTANT_CHAT = "assistant.chat"
    MEMORY_REVIEW = "memory.review"
    MEMORY_FEEDBACK = "memory.feedback"
    SOURCES_GET = "sources.get"
    SOURCES_CONFIGURE = "sources.configure"
    SETTINGS_MODEL = "settings.model"
    ONBOARDING_BOOTSTRAP = "onboarding.bootstrap"
    ASSISTANT_FIRST_INSIGHT = "assistant.first_insight"

    # Smart agents (v0.2)
    ANALYZE_BEHAVIOR = "analyze.behavior"
    CLASSIFY_PRIORITY = "classify.priority"

    # Triage (v0.3)
    TRIAGE_FILES = "triage.files"

    # System
    SYS_STATUS = "sys.status"
    SYS_PING = "sys.ping"


# Maps syscall types to the agent task names they dispatch to
SYSCALL_TO_AGENT: dict[str, str] = {
    SyscallType.FILE_SEARCH: "file_search",
    SyscallType.FILE_READ: "file_read",
    SyscallType.FILE_LIST: "file_list",
    SyscallType.FILE_SUMMARIZE: "summarizer",
    SyscallType.KNOWLEDGE_QUERY: "knowledge_query",
    SyscallType.KNOWLEDGE_STORE: "knowledge_store",
    SyscallType.CONTEXT_SAVE: "context",
    SyscallType.CONTEXT_LOAD: "context",
    SyscallType.AGENT_SUBMIT: "agent_submit",
    SyscallType.AGENT_STATUS: "agent_task_status",
    SyscallType.REPORT_DAILY: "report_generator",
    SyscallType.REPORT_PROJECT: "report_generator",
    SyscallType.REPORT_BRIEF: "report_generator",
    SyscallType.PROFILE_GET: "profile_builder",
    SyscallType.PROFILE_SUMMARY: "assistant",
    SyscallType.ASSISTANT_CHAT: "assistant",
    SyscallType.MEMORY_REVIEW: "assistant",
    SyscallType.MEMORY_FEEDBACK: "assistant",
    SyscallType.SOURCES_GET: "assistant",
    SyscallType.SOURCES_CONFIGURE: "assistant",
    SyscallType.SETTINGS_MODEL: "assistant",
    SyscallType.ONBOARDING_BOOTSTRAP: "assistant",
    SyscallType.ASSISTANT_FIRST_INSIGHT: "assistant",
    SyscallType.ANALYZE_BEHAVIOR: "behavior_analyzer",
    SyscallType.CLASSIFY_PRIORITY: "priority_classifier",
    SyscallType.TRIAGE_FILES: "triage",
    SyscallType.SYS_STATUS: "system_status",
}


@dataclass
class SyscallRequest:
    """Incoming request from an external agent."""
    call_type: str
    params: dict[str, Any] = field(default_factory=dict)
    caller: str = "unknown"
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    priority: int = 1

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str | bytes) -> SyscallRequest:
        d = json.loads(data)
        return cls(**d)


@dataclass
class SyscallResponse:
    """Response back to the external agent."""
    request_id: str
    success: bool
    data: Any = None
    error: str | None = None
    elapsed_ms: float = 0
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str | bytes) -> SyscallResponse:
        d = json.loads(data)
        return cls(**d)
