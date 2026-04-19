"""Configuration loader — reads YAML config and expands paths."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p)).expanduser().resolve()


@dataclass
class KernelConfig:
    name: str = "AgentOS"
    version: str = "0.1.0"
    pid_file: Path = Path("/tmp/agent_sys.pid")
    socket_path: Path = Path("/tmp/agent_sys.sock")
    log_level: str = "INFO"
    log_file: Path = Path("~/.agent_sys/logs/sysagent.log")


@dataclass
class FilesystemConfig:
    watch_paths: list[Path] = field(default_factory=lambda: [Path("~/Documents")])
    ignore_patterns: list[str] = field(default_factory=lambda: ["node_modules", ".git", "__pycache__"])
    ignore_subpaths: list[str] = field(default_factory=list)
    index_extensions: list[str] = field(default_factory=lambda: [".py", ".md", ".txt", ".json"])
    scan_interval_seconds: int = 300
    max_file_size_mb: int = 10


@dataclass
class EmbeddingConfig:
    provider: str = "dashscope"
    local_model: str = "all-MiniLM-L6-v2"
    api_key_env: str = "DASHSCOPE_API_KEY"
    api_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen3-vl-embedding"
    dimensions: int = 0


@dataclass
class MemoryConfig:
    db_path: Path = Path("~/.agent_sys/memory.db")
    cache_max_items: int = 10000
    use_local_embeddings: bool = True
    local_model: str = "all-MiniLM-L6-v2"
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)


@dataclass
class SchedulerConfig:
    max_concurrent_agents: int = 4
    default_timeout_seconds: int = 120
    priority_levels: int = 3


@dataclass
class SyscallConfig:
    transport: str = "unix_socket"
    http_port: int = 7437
    auth_token_path: Path = Path("~/.agent_sys/auth_token")
    allowed_callers: list[str] = field(default_factory=lambda: ["cursor", "claude_code", "cli"])


@dataclass
class LLMProviderConfig:
    api_key_env: str = ""
    base_url: str = ""
    models: dict[str, str] = field(default_factory=dict)


@dataclass
class LLMConfig:
    default_provider: str = "openai"
    providers: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        "openai": {"api_key_env": "OPENAI_API_KEY", "models": {"fast": "gpt-4o-mini", "strong": "gpt-4o"}},
        "anthropic": {"api_key_env": "ANTHROPIC_API_KEY", "models": {"fast": "claude-sonnet-4-20250514", "strong": "claude-opus-4-20250514"}},
    })
    routing: dict[str, str] = field(default_factory=lambda: {
        "summarize": "fast", "analyze": "strong", "classify": "fast",
        "report": "strong", "profile": "strong", "search": "fast",
    })
    defaults: dict[str, str] = field(default_factory=lambda: {
        "text_provider": "",
        "text_tier": "fast",
        "vision_provider": "",
        "vision_tier": "vision",
    })
    functions: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class CronJobConfig:
    agent: str = ""
    trigger: str = "interval"     # interval | daily | weekly | after_scan | after:<agent>
    interval_hours: float = 6
    time: str = "09:00"


@dataclass
class AdaptiveConfig:
    enabled: bool = True
    default_interval_minutes: int = 30
    min_interval_minutes: int = 10
    max_interval_minutes: int = 240
    deep_analysis_hour: int = 3
    strategy: str = "balanced"  # aggressive | balanced | quiet | custom


@dataclass
class CronConfig:
    enabled: bool = True
    jobs: list[dict[str, Any]] = field(default_factory=list)
    adaptive: AdaptiveConfig = field(default_factory=AdaptiveConfig)


@dataclass
class TriageConfig:
    """User-tunable knobs for the triage agent.

    `skip_path_patterns` is a hard filter (bypasses LLM). `file_type_priority`
    and `hints` influence ordering and LLM decisions but do not override
    per-file semantic judgment.
    """
    skip_path_patterns: list[str] = field(default_factory=list)
    file_type_priority: dict[str, int] = field(default_factory=dict)
    hints: list[str] = field(default_factory=list)


@dataclass
class AgentOSConfig:
    kernel: KernelConfig = field(default_factory=KernelConfig)
    filesystem: FilesystemConfig = field(default_factory=FilesystemConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    syscall: SyscallConfig = field(default_factory=SyscallConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    cron: CronConfig = field(default_factory=CronConfig)
    triage: TriageConfig = field(default_factory=TriageConfig)


def _build_section(cls: type, data: dict[str, Any] | None):
    if data is None:
        return cls()
    cleaned: dict[str, Any] = {}
    for k, v in data.items():
        if k in {f.name for f in cls.__dataclass_fields__.values()}:
            ftype = cls.__dataclass_fields__[k].type
            if ftype in ("Path", "pathlib.Path") or "Path" in str(ftype):
                if isinstance(v, list):
                    v = [_expand(str(p)) for p in v]
                elif isinstance(v, str):
                    v = _expand(v)
            cleaned[k] = v
    return cls(**cleaned)


def load_config(path: str | Path | None = None) -> AgentOSConfig:
    """Load configuration from YAML file, falling back to defaults."""
    if path is None:
        path = Path(__file__).parent.parent.parent / "config" / "default.yaml"
    path = Path(path)

    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    llm_raw = raw.get("llm", {})
    _llm_defaults = LLMConfig()
    llm_config = LLMConfig(
        default_provider=llm_raw.get("default_provider", "openai"),
        providers=llm_raw.get("providers", _llm_defaults.providers),
        routing=llm_raw.get("routing", _llm_defaults.routing),
        defaults=llm_raw.get("defaults", _llm_defaults.defaults),
        functions=llm_raw.get("functions") or {},
    )

    # Embedding config
    mem_raw = raw.get("memory", {})
    emb_raw = mem_raw.get("embedding", {})
    embedding_config = EmbeddingConfig(
        provider=emb_raw.get("provider", "local"),
        local_model=emb_raw.get("local_model", mem_raw.get("local_model", "all-MiniLM-L6-v2")),
        api_key_env=emb_raw.get("api_key_env", ""),
        api_base_url=emb_raw.get("api_base_url", ""),
        model=emb_raw.get("model", ""),
        dimensions=emb_raw.get("dimensions", 0),
    )

    cron_raw = raw.get("cron", {})
    adaptive_raw = cron_raw.get("adaptive", {})
    adaptive_config = AdaptiveConfig(
        enabled=adaptive_raw.get("enabled", True),
        default_interval_minutes=adaptive_raw.get("default_interval_minutes", 30),
        min_interval_minutes=adaptive_raw.get("min_interval_minutes", 10),
        max_interval_minutes=adaptive_raw.get("max_interval_minutes", 240),
        deep_analysis_hour=adaptive_raw.get("deep_analysis_hour", 3),
        strategy=adaptive_raw.get("strategy", "balanced"),
    )
    cron_config = CronConfig(
        enabled=cron_raw.get("enabled", True),
        jobs=cron_raw.get("jobs", []),
        adaptive=adaptive_config,
    )

    memory_config = _build_section(MemoryConfig, raw.get("memory"))
    memory_config.embedding = embedding_config

    triage_raw = raw.get("triage", {}) or {}
    triage_config = TriageConfig(
        skip_path_patterns=triage_raw.get("skip_path_patterns", []) or [],
        file_type_priority=triage_raw.get("file_type_priority", {}) or {},
        hints=triage_raw.get("hints", []) or [],
    )

    return AgentOSConfig(
        kernel=_build_section(KernelConfig, raw.get("kernel")),
        filesystem=_build_section(FilesystemConfig, raw.get("filesystem")),
        memory=memory_config,
        scheduler=_build_section(SchedulerConfig, raw.get("scheduler")),
        syscall=_build_section(SyscallConfig, raw.get("syscall")),
        llm=llm_config,
        cron=cron_config,
        triage=triage_config,
    )
