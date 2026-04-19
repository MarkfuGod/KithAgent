# AgentOS — Complete Codebase Reference

> A system-level agent daemon that maps traditional OS architecture to an LLM-Agent runtime.
> Version 0.2.0 | Python 3.11+ | MIT License

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Directory Structure](#directory-structure)
3. [Configuration](#configuration)
4. [Kernel Layer](#kernel-layer)
5. [Agent System](#agent-system)
6. [LLM Abstraction Layer](#llm-abstraction-layer)
7. [Memory & Storage](#memory--storage)
8. [Filesystem Watcher](#filesystem-watcher)
9. [Scheduler](#scheduler)
10. [Syscall Interface](#syscall-interface)
11. [Web Dashboard](#web-dashboard)
12. [CLI](#cli)
13. [File Extractors](#file-extractors)
14. [Data Flow](#data-flow)
15. [Dependencies](#dependencies)

---

## Architecture Overview

AgentOS maps traditional operating system concepts onto an LLM-agent architecture:

| Traditional OS | AgentOS Equivalent | Module |
|---|---|---|
| Kernel / init | `SysAgentKernel` | `src/kernel/daemon.py` |
| Thread | `AgentTask` | `src/agents/base.py` |
| Process Scheduler | `AgentScheduler` | `src/scheduler/pool.py` |
| VFS / Filesystem | `FileSystemWatcher` | `src/filesystem/watcher.py` |
| RAM + Disk | `MemoryStore` (LRU + SQLite) | `src/memory/store.py` |
| System Calls | `SyscallServer` (Unix socket + HTTP) | `src/syscall/server.py` |
| Cron | `CronScheduler` (LLM-adaptive) | `src/kernel/cron.py` |
| Event Bus / dmesg | `EventBus` (pub/sub + ring buffer) | `src/kernel/event_bus.py` |

```
External Agents (Cursor / Claude Code / OpenClaw)
        │
   ┌────▼────┐
   │ Syscall │  Unix Socket (/tmp/agent_sys.sock)
   │   API   │  HTTP (127.0.0.1:7437)
   └────┬────┘
        │
┌───────▼─────────────────────────────────────────┐
│              SysAgentKernel (daemon.py)          │
│                                                  │
│  EventBus ← Memory ← LLM Router ← Filesystem   │
│       ↓         ↓         ↓           ↓         │
│   Scheduler ← CronScheduler ← SyscallServer     │
│       │                                          │
│  ┌────▼──────────────────────────────────────┐   │
│  │         Smart Agent Pool (12 agents)      │   │
│  │  Triage · Summarizer · Analyzer           │   │
│  │  Reporter · ProfileBuilder · Prioritizer  │   │
│  │  FileSearch · FileRead · FileList         │   │
│  │  KnowledgeQuery · KnowledgeStore          │   │
│  │  Context · SystemStatus                   │   │
│  │  AgentSubmit · AgentTaskStatus            │   │
│  └───────────────────────────────────────────┘   │
│                                                  │
│  LLM Router ── OpenAI / Anthropic / Compatible   │
│  Embeddings ── local / dashscope / openai        │
│                                                  │
│  ~/.agent_sys/memory.db (SQLite persistent)      │
└──────────────────────────────────────────────────┘

Web Dashboard (127.0.0.1:7438) — reads SQLite directly
```

---

## Directory Structure

```
agent_sys/
├── config/
│   └── default.yaml              # Master config (kernel, filesystem, memory, LLM, cron)
├── src/
│   ├── __init__.py               # Package root, version = "0.2.0"
│   ├── cli.py                    # CLI entry point (15 subcommands)
│   ├── extractors.py             # PDF/DOCX/image content extraction
│   ├── kernel/
│   │   ├── __init__.py
│   │   ├── config.py             # Dataclass config loader (YAML → typed objects)
│   │   ├── daemon.py             # SysAgentKernel — boot, run, shutdown lifecycle
│   │   ├── cron.py               # CronScheduler — fixed triggers + LLM-adaptive loop
│   │   └── event_bus.py          # EventBus — pub/sub with SSE support
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py               # LLMMessage, LLMResponse, LLMProvider ABC
│   │   ├── openai_adapter.py     # OpenAI SDK + HTTP fallback
│   │   ├── claude_adapter.py     # Anthropic SDK + HTTP + compatible endpoint
│   │   ├── compatible_adapter.py # OpenAI-compatible (Ollama, DeepSeek, Groq, etc.)
│   │   └── router.py             # ModelRouter — task-type→provider/tier routing
│   ├── filesystem/
│   │   ├── __init__.py
│   │   └── watcher.py            # Full scan + watchdog realtime + periodic rescan
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── store.py              # MemoryStore — LRU cache + SQLite cold store
│   │   └── embeddings.py         # Pluggable vector embeddings (local/API)
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── pool.py               # AgentScheduler — priority queue + concurrency
│   ├── syscall/
│   │   ├── __init__.py
│   │   ├── protocol.py           # SyscallType enum, request/response dataclasses
│   │   ├── server.py             # Unix socket + HTTP server + SSE events
│   │   └── client.py             # Async + Sync client SDK
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py               # AgentState, AgentTask, BaseAgent ABC
│   │   ├── builtin.py            # 14 built-in agents + BUILTIN_AGENTS registry
│   │   ├── triage.py             # TriageAgent — LLM file importance classification
│   │   ├── summarizer.py         # SummarizerAgent — multimodal file summaries
│   │   ├── analyzer.py           # BehaviorAnalyzerAgent — holistic user analysis
│   │   ├── prioritizer.py        # PriorityClassifierAgent — P0/P1/P2 recency tiers
│   │   ├── reporter.py           # ReportGeneratorAgent — daily/quick/project/brief
│   │   └── profile_builder.py    # ProfileBuilderAgent — whole-person JSON profile
│   └── web/
│       ├── __init__.py
│       ├── dashboard.py          # aiohttp backend (20+ API endpoints)
│       ├── dashboard.html        # Dark-theme SPA (7 tabs)
│       ├── chart.min.js          # Chart.js for visualizations
│       └── routing-ui.js         # LLM routing configuration UI
├── pyproject.toml                # Build config, entry point: agent-sys = src.cli:main
├── requirements.txt              # Dependencies
├── README.md                     # User-facing docs (Chinese)
├── CONVERSATION_SUMMARY.md       # Development conversation log
└── ROUND_DIALOG_SUMMARY_2026-04-14.md
```

---

## Configuration

### `config/default.yaml`

The master configuration file defines all subsystem parameters. Loaded by `src/kernel/config.py` into typed dataclass hierarchies.

#### Kernel

```yaml
kernel:
  name: AgentOS
  version: 0.2.0
  pid_file: /tmp/agent_sys.pid
  socket_path: /tmp/agent_sys.sock
  log_level: INFO
  log_file: ~/.agent_sys/logs/sysagent.log
```

#### Filesystem

Watches the entire home directory with extensive ignore patterns. Indexes 28 file extensions covering code, documents, images, and data formats.

Two kinds of ignore:
- `ignore_patterns` — matches single directory/file **names** via `fnmatch` (e.g. `node_modules` matches any directory anywhere named `node_modules`)
- `ignore_subpaths` — matches **path substrings** so we can prune specific subtrees under otherwise-useful parents (e.g. `.cursor/extensions` prunes IDE plugins without hiding user-written rules elsewhere under `.cursor`)

```yaml
filesystem:
  watch_paths: ["~"]
  ignore_patterns:
    - node_modules, .git, __pycache__, .DS_Store, *.pyc
    - .venv, venv, env, Library, .Trash, .cache, .npm
    - .cargo, .rustup, .tox, dist, build, .eggs, *.egg-info
    - .local, .oh-my-zsh, .conda, anaconda3, opt
    - .docker, .kube, Movies, Music, Pictures, .agent_sys
  ignore_subpaths:
    - .cursor/extensions, .vscode/extensions, .cursor-server
    - go/pkg, .gradle/caches, .m2/repository
  index_extensions:
    - Code: .py .js .ts .go .rs .sh .java .c .cpp .swift
    - Data: .json .yaml .yml .toml .xml .csv
    - Docs: .md .txt .rst .tex .pdf .docx .doc .pptx .xlsx
    - Images: .png .jpg .jpeg .gif .webp
  scan_interval_seconds: 300
  max_file_size_mb: 10
```

#### Memory

SQLite-backed with an LRU hot cache. Supports pluggable vector embedding providers.

```yaml
memory:
  db_path: ~/.agent_sys/memory.db
  cache_max_items: 10000
  embedding:
    provider: dashscope          # local | dashscope | openai
    api_key_env: DASHSCOPE_API_KEY
    api_base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    model: qwen3-vl-embedding
```

#### LLM

Four provider types with per-function routing. The router resolves provider and model tier in a cascading priority: explicit params → per-function config → global defaults → legacy routing table.

```yaml
llm:
  default_provider: openai
  providers:
    openai:          { models: { fast: gpt-4o-mini, strong: gpt-4o, vision: gpt-4o } }
    anthropic:       { models: { fast: claude-sonnet-4-20250514, strong: claude-opus-4-20250514 } }
    openai_compatible:      { models: { fast: qwen-plus, strong: qwen-plus, vision: qwen-vl-plus } }
    anthropic_compatible:   { models: { fast: MiniMax-M2.7, strong: MiniMax-M2.7 } }
  defaults:
    text_tier: fast
    vision_provider: openai_compatible
    vision_tier: vision
  functions:
    triage:    { text_tier: fast }
    summarize: { text_tier: fast, vision_provider: openai_compatible, vision_model: qwen-vl-plus }
  routing:
    summarize: fast, analyze: strong, classify: fast
    report: strong, profile: strong, search: fast, vision: vision
```

#### Cron (Adaptive Scheduling)

Dual scheduling: fixed triggers (after_scan, after:agent, daily) and an LLM-driven adaptive loop that gathers activity snapshots and decides what to run.

```yaml
cron:
  enabled: true
  adaptive:
    enabled: true
    default_interval_minutes: 30
    min_interval_minutes: 10
    max_interval_minutes: 240
    deep_analysis_hour: 3
    strategy: balanced              # aggressive | balanced | quiet
  jobs:
    - { agent: triage,             trigger: after_scan }
    - { agent: summarizer,         trigger: "after:triage" }
    - { agent: behavior_analyzer,  trigger: interval, interval_hours: 6 }
    - { agent: priority_classifier, trigger: "after:behavior_analyzer" }
    - { agent: report_generator,   trigger: daily, time: "09:00" }
    - { agent: profile_builder,    trigger: weekly }
```

#### Triage (Mission-driven Prioritization)

Controls what triage treats as guaranteed-noise (hard filter) vs. which files should be analyzed first when LLM budget is limited (soft preference). Separates filtering from ranking so user intent can drive the latter without hurting correctness of the former.

```yaml
triage:
  # Hard filter — path substrings that are auto-classified as 'skip' before any
  # LLM call. Extends (does not replace) filesystem.ignore_patterns.
  skip_path_patterns:
    - site-packages/, /vendor/, /third_party/, /node_modules/
    - /.git/objects/, /__pycache__/, /dist/, /build/
    - /.cursor/extensions/, /.vscode/extensions/, /.cursor-server/
    - /.gradle/caches/, /.m2/repository/, /go/pkg/
  # Soft preference — file-type priority (1-10). Higher means user is more
  # likely to care. Used for ORDERING: when token budget runs out, high-prio
  # types get analyzed first. Also injected into the LLM prompt as hints.
  # NOT a hard rule: a .txt file named 'journal.txt' can still be 'high'.
  file_type_priority:
    .md: 9, .docx: 9, .pdf: 8, .rst: 8, .tex: 8
    .py: 7, .ts: 7, .go: 7, .rs: 7, .swift: 7
    .java: 6, .c: 6, .cpp: 6, .js: 6, .sh: 6
    .json: 4, .yaml: 4, .yml: 4, .toml: 4
    .png: 5, .jpg: 5, .jpeg: 5
    .txt: 3, .csv: 2, .xml: 3, .gif: 3, .webp: 3
  # Free-form user intent — injected into the LLM triage system prompt.
  hints:
    - "My own source code projects and personal notes are the most important."
    - "Plain .txt files are usually temporary scratch notes."
    - "Downloaded PDFs/docs are often learning material."
    - "Anything under node_modules/site-packages/vendor is junk — always skip."
```

### Config Dataclasses — `src/kernel/config.py`

All configuration is loaded into typed dataclasses via `load_config()`:

| Dataclass | Fields |
|---|---|
| `AgentOSConfig` | kernel, filesystem, memory, scheduler, syscall, llm, cron, triage |
| `KernelConfig` | name, version, pid_file, socket_path, log_level, log_file |
| `FilesystemConfig` | watch_paths, ignore_patterns, ignore_subpaths, index_extensions, scan_interval_seconds, max_file_size_mb |
| `TriageConfig` | skip_path_patterns, file_type_priority, hints |
| `MemoryConfig` | db_path, cache_max_items, use_local_embeddings, local_model, embedding: `EmbeddingConfig` |
| `EmbeddingConfig` | provider, local_model, api_key_env, api_base_url, model, dimensions |
| `SchedulerConfig` | max_concurrent_agents (4), default_timeout_seconds (120), priority_levels (3) |
| `SyscallConfig` | transport, http_port (7437), auth_token_path, allowed_callers |
| `LLMConfig` | default_provider, providers, routing, defaults, functions |
| `CronConfig` | enabled, jobs, adaptive: `AdaptiveConfig` |
| `AdaptiveConfig` | enabled, default_interval_minutes, min/max_interval_minutes, deep_analysis_hour, strategy |

---

## Kernel Layer

### `src/kernel/daemon.py` — SysAgentKernel

The main orchestrator — analogous to `systemd`/`launchd`. Manages the full lifecycle of all subsystems.

**Boot order** (dependency-driven):
1. Ensure directories + write PID file
2. Install SIGTERM/SIGINT signal handlers
3. **EventBus** — central pub/sub
4. **MemoryStore** — SQLite + LRU cache
5. **Embeddings** — configure provider (local/API)
6. **LLM Router** — register all providers, set event bus
7. **FileSystemWatcher** — start background scan
8. **AgentScheduler** — start dispatch loop
9. **CronScheduler** — start adaptive + fixed trigger loops
10. **SyscallServer** — start Unix socket + HTTP listener

**Shutdown** reverses boot order. Safe to call multiple times.

**Key methods:**
- `boot()` — initialize all subsystems
- `run()` — boot + sleep loop + shutdown
- `shutdown()` — graceful reverse teardown
- `reload_config()` — hot-reload LLM router, embeddings, and cron strategy without restart
- `get_memory()`, `get_filesystem()`, `get_scheduler()`, `get_llm()`, `get_event_bus()` — subsystem accessors
- `status()` — returns kernel name, version, PID, running state, subsystem list, filesystem stats

**Hot reload** (`reload_config`):
- Re-reads `config/default.yaml`
- Merges `~/.agent_sys/llm_config.yaml` user overrides
- Rebuilds LLM router with new providers
- Reconfigures embedding engine
- Updates cron strategy on the live instance

### `src/kernel/event_bus.py` — EventBus

Lightweight asyncio pub/sub with a ring buffer for reconnects.

**Events emitted throughout the system:**
- `task.started`, `task.completed`, `task.failed` — agent lifecycle
- `llm.request`, `llm.response` — LLM calls with model, tokens, latency
- `triage.batch_progress`, `summarize.file_progress` — agent progress
- `config.reloaded` — after hot reload

**Key types:**
- `AgentEvent` — dataclass with `event_type`, `data` dict, `timestamp`. Methods: `to_sse()`, `to_dict()`
- `EventBus` — `emit()`, `emit_dict()`, `subscribe()` (returns `asyncio.Queue`), `unsubscribe()`, `recent_events()`
- Ring buffer capped at 500 events (configurable)
- Dead subscriber queues auto-pruned on `QueueFull`

### `src/kernel/cron.py` — CronScheduler

Dual scheduling model combining predictable triggers with intelligent adaptation.

**Fixed triggers:**
- `after_scan` — polls `filesystem._stats["last_scan"]` to detect scan completions
- `after:<agent>` — polls `_last_run` dict to chain agents
- `daily` — sleeps until target time, then dispatches

**Adaptive loop** (LLM-driven, new in v0.3):
1. Wait 30s for subsystems to settle
2. `_gather_activity_snapshot()` — collects modification rates, recent files, triage stats, summary progress, embedding status, LLM router stats, past scheduling decisions
3. `_llm_decide_schedule()` — sends snapshot to LLM with `_SCHEDULER_SYSTEM` prompt, expects JSON with `mode`, `next_interval_minutes`, `agents_to_run` or `stages`, and `reasoning`
4. Falls back to `_default_decision()` which uses the strategy config (aggressive/balanced/quiet) with rule-based policies
5. Dispatches agents, persists decision to knowledge DB
6. Sleeps for LLM-recommended interval (clamped to min/max)

**Scheduling strategies** (defined in `SCHEDULING_STRATEGIES`):

| Strategy | Triage | Summarize | Report | Behavior | Profile |
|---|---|---|---|---|---|
| **aggressive** | always | always | always | frequent | daily |
| **balanced** | always | active_only | daily_only | daily | weekly |
| **quiet** | deep_hour_only | deep_hour_only | daily_only | deep_hour_only | deep_hour_only |

**Staged execution:**
- `stages` field allows sequential groups where agents within a stage run in parallel via `scheduler.fan_out()`
- Between stages, `_gate_next_stage()` optionally asks the LLM whether to proceed, adjust parameters, or skip — based on prior stage results

**Safety mechanisms:**
- Cooldown check: skips agents that ran < 60s ago
- Active check: skips agents already running
- Minimum timeouts per agent type (300s for most, 180s for priority_classifier)

---

## Agent System

### `src/agents/base.py` — Base Types

**`AgentState`** (str enum): `PENDING → RUNNING → COMPLETED | FAILED | CANCELLED` (also `BLOCKED`)

**`AgentTask`** dataclass — the unit of work:
- `task_id` — auto-generated UUID hex[:12]
- `name` — maps to agent `name` field
- `priority` — 0 (high), 1 (normal), 2 (low)
- `state` — lifecycle enum
- `input_data` — dict of parameters
- `result` — set on completion
- `error` — set on failure
- `caller` — originating external agent
- `parent_task_id` / `children_ids` — for fan-out patterns
- `elapsed()` — computed duration

**`BaseAgent`** ABC:
- `name: str` — class attribute, matched by scheduler
- `execute(task, context) -> Any` — abstract method
- `can_handle(task_name) -> bool` — default: `task_name == self.name`

**Context dict** passed to all agents:
```python
{
    "memory": MemoryStore,
    "scheduler": AgentScheduler,
    "filesystem": FileSystemWatcher,
    "kernel": SysAgentKernel,
    "llm": ModelRouter,
    "event_bus": EventBus,
}
```

### `src/agents/builtin.py` — Built-in Agents Registry

14 agents registered in `BUILTIN_AGENTS` list:

**v0.1 — Rule-based (no LLM):**
| Agent | Name | Purpose |
|---|---|---|
| `FileSearchAgent` | `file_search` | SQL LIKE + vector search on file index |
| `FileReadAgent` | `file_read` | Read single file metadata from memory |
| `FileListAgent` | `file_list` | List files by directory/type/triage with SQL |
| `KnowledgeQueryAgent` | `knowledge_query` | Query knowledge table by category |
| `KnowledgeStoreAgent` | `knowledge_store` | Insert into knowledge table |
| `ContextAgent` | `context` | Save/load cross-session agent context with TTL |
| `SystemStatusAgent` | `system_status` | Aggregate status from all subsystems |

**v0.2 — LLM-powered smart agents:**
| Agent | Name | Purpose |
|---|---|---|
| `SummarizerAgent` | `summarizer` | Multimodal file summaries (code/doc/image) |
| `BehaviorAnalyzerAgent` | `behavior_analyzer` | Holistic user behavior analysis (work/study/personal) |
| `PriorityClassifierAgent` | `priority_classifier` | P0/P1/P2 recency-based classification |
| `ReportGeneratorAgent` | `report_generator` | Daily, quick, project, and brief reports |
| `ProfileBuilderAgent` | `profile_builder` | Whole-person JSON profile from filesystem stats |

**v0.3 — LLM triage:**
| Agent | Name | Purpose |
|---|---|---|
| `TriageAgent` | `triage` | LLM-driven file importance classification |

**v0.5 — Agent management:**
| Agent | Name | Purpose |
|---|---|---|
| `AgentSubmitAgent` | `agent_submit` | Proxy: submit arbitrary agent tasks via syscall |
| `AgentTaskStatusAgent` | `agent_task_status` | Proxy: query task status by ID |

### `src/agents/triage.py` — TriageAgent

Classifies indexed files into `high`/`medium`/`low`/`skip` to decide what's worth spending LLM tokens to summarize. Separates filtering (rules) from prioritization (mission + user intent).

**Phase 1 — Rule-based fast pass:**
Reads `config.triage.skip_path_patterns` (no hardcoded list — see Triage config block) and applies each as a SQL `LIKE '%pattern%'` substring match, marking everything as `skip`. Zero LLM cost. Falls back to a minimal built-in list when config is empty.

**Phase 2 — LLM batch triage with user-preference ordering:**
1. Fetch untriaged files via `get_untriaged_files(type_priority=...)` — rows are ordered by `file_type_priority` first, `modified_at DESC` second, so the user's preferred file types get analyzed first when budget runs out
2. Group by 3-level directory prefix under home for directory-context batching
3. Compose system prompt once per run via `_build_triage_prompt(hints, type_priority)` — injects the fixed mission text + `USER PREFERENCES` block + `File-type priority hints` block
4. For each group, send paths + metadata to LLM
5. LLM returns JSON with `bulk` prefix rules and `individual` overrides
6. Apply bulk rules via `batch_update_triage_by_prefix`, individuals via `batch_update_triage`

**Triage levels:**
- `high` — user's own creation (personal projects, notes, custom configs)
- `medium` — useful context (dependency configs, data files, scaffolding)
- `low` — generic code anyone could have (common templates, boilerplate)
- `skip` — third-party source, generated output, binary metadata

**Mission + user intent coupling:**
The LLM is explicitly told that `file_type_priority` and `hints` are **preference hints, not rules** — a `.txt` file named `journal.txt` can still be classified `high` even if the user flagged `.txt` as low-priority. Preferences only bias ordering and tie-breaking; per-file semantic judgment still wins.

**Resilience:** Tolerant JSON parsing strips markdown fences and scans backwards for the last valid `}`.

**Time budget:** Configurable via `task.input_data["time_budget"]` (default 300s). Emits `triage.batch_progress` events.

### `src/agents/summarizer.py` — SummarizerAgent

Generates semantic summaries for indexed files. Respects triage results — only processes `high`, `medium`, and untriaged files.

**Two modes:**

| Mode | Method | Speed | Quality |
|---|---|---|---|
| `deep` (default) | Reads file content, sends preview to LLM | Slower | High |
| `light` | LLM infers from metadata only (path, type, size, mtime) | Fast | Lower |

**Deep mode pipeline per file:**
1. **Image?** → `extract_content()` for data URI → vision model with `_VISION_SYSTEM` prompt → prefix `[vision]`
2. **Document?** → `extract_content()` for text or fallback to vision for scanned PDFs → `_DOC_SYSTEM` prompt → prefix `[doc]`
3. **Text?** → `_read_preview()` (first 4000 chars) → `_DEEP_SYSTEM` prompt

**Light mode:** Batches of 10 files, sends metadata JSON array, parses JSON response, prefixes stored text with `[light]`.

**Post-processing:**
- **Hierarchical summaries:** Groups file summaries by project directory (from `get_project_directories`), produces project-level summaries stored as `project_summary` in knowledge table. Only runs if >30s budget remains.
- **Embeddings:** Computes vector embeddings for newly summarized files via `src.memory.embeddings` if available.

**Time-budgeted incremental execution:** Processes files one by one, persists each result immediately, stops when budget exhausted. Next cron cycle picks up where it left off.

### `src/agents/analyzer.py` — BehaviorAnalyzerAgent

Holistic analysis across three dimensions of the user's digital life.

**Data gathering:**
- File modification stats (type distribution)
- Project directories (by marker files)
- Directory activity and content breakdown
- Recent files (last 7 days + last 6 hours)
- Document and image file samples
- Prior project summaries from knowledge table

**LLM analysis** produces structured JSON:
```json
{
  "dimensions": {
    "work": { "projects": [...], "primary_skills": [...], "current_focus": "..." },
    "study": { "topics": [...], "resources": [...], "learning_stage": "..." },
    "personal": { "documents": "...", "media": "...", "hobbies": [...] }
  },
  "file_landscape": { "total_files": N, "by_category": {...} },
  "personality_profile": "2-3 sentence summary",
  "work_patterns": { "active_hours": "...", "style": "..." },
  "recommendations": [...]
}
```

**Fallback:** Rule-based analysis when no LLM available — produces language stats, project list, and file counts.

Results stored in knowledge table under `behavior_insight` category.

### `src/agents/prioritizer.py` — PriorityClassifierAgent

Non-LLM agent that classifies all indexed files into three recency tiers:

| Priority | Label | Threshold |
|---|---|---|
| P0 | Hot | Modified < `hot_days` (default 3) ago |
| P1 | Warm | Modified < `warm_days` (default 30) ago |
| P2 | Cold | Older than warm threshold |

Bulk updates via `batch_update_priorities`. Reports distribution globally and by category (code/document/image/other).

### `src/agents/reporter.py` — ReportGeneratorAgent

Four report types, all stored in the knowledge table:

**`daily`** — Full day review:
- Gathers 24h activity, directory breakdown, time distribution
- LLM produces JSON with dimensions (work/study/personal), highlights, time patterns
- Stored as `daily_report_YYYYMMDD`

**`quick`** — Lightweight status snapshot:
- Last hour activity, modification rate, breakdown by type
- Stored as `quick_report_{timestamp}`

**`project`** — Per-project deep dive:
- File type distribution, key files list
- LLM adds tech_stack, description, entry_points, content_types
- Stored as `project_profile_{dir}`

**`brief`** — Context briefing for new agent sessions:
- Combines latest behavior insight, user profile, recent files
- LLM produces JSON with `who`, `current_focus`, `recent_activity`, `key_files`, `preferences`
- Stored as `context_brief_{timestamp}`

All report types have rule-based fallbacks when no LLM is available.

### `src/agents/profile_builder.py` — ProfileBuilderAgent

Builds a persistent whole-person user profile from filesystem statistics.

**Data gathering:**
- File type distribution, directory breakdown/activity
- Recent files (last 7 days)
- Discovered projects (by marker files)
- Document and image file samples
- Config file filenames (package.json, requirements.txt, Cargo.toml, pyproject.toml)

**LLM profile** produces comprehensive JSON:
```json
{
  "identity": { "summary": "...", "roles": [...] },
  "technical": {
    "primary_languages": [{"language": "...", "file_count": N, "confidence": "..."}],
    "frameworks": [...], "tools": [...],
    "coding_style": { "naming_convention": "...", "project_structure": "..." }
  },
  "projects": [{"name": "...", "path": "...", "status": "...", "category": "..."}],
  "interests": { "professional": [...], "academic": [...], "personal": [...] },
  "digital_footprint": { "total_files": N, "content_mix": {...}, "organization_style": "..." },
  "work_patterns": { "most_active_hours": "...", "productivity_style": "..." },
  "expertise_areas": [...]
}
```

**Rule-based fallback:** Extension → language mapping, content mix counts, project list stub.

Stored in knowledge table as `kid="user_profile_current"`, `category="user_profile"`.

---

## LLM Abstraction Layer

### `src/llm/base.py` — Core Types

**`LLMMessage`** — `role` (system/user/assistant) + `content` (str for text, list[dict] for multimodal)

**`LLMResponse`** — `content`, `model`, `provider`, `usage` dict (prompt_tokens, completion_tokens), `raw`

**`LLMProvider`** ABC:
- `complete(messages, model, temperature, max_tokens) -> LLMResponse`
- `available() -> bool`
- `list_models() -> dict[str, str]` — tier → model name mapping

### `src/llm/router.py` — ModelRouter

Central dispatcher that selects provider and model tier per request.

**Resolution order** (most to least specific):
1. Explicit `provider_name` / `model` parameters in `complete()`
2. Per-function config (`functions[task_type]` in YAML)
3. Global defaults (`defaults.text_provider` / `vision_provider`)
4. Legacy routing table (`task_type → tier` on default provider)

**Circuit breaker:** After 3 consecutive auth failures (401/unauthorized) for a provider, trips a 300s cooldown. Resets on successful call. `reset_circuit_breakers()` clears manually.

**Vision safety:** Refuses to fall back to a non-vision provider when `is_vision=True` — requires the explicitly configured vision provider to be available.

**Event emission:** Emits `llm.request` (pre-call) and `llm.response` (post-call with usage/latency) on the EventBus.

**Task type aliases:** `classify` maps to `triage` in function config lookup.

**Factory:** `create_router_from_config(llm_config)` instantiates all configured providers and builds the router.

### `src/llm/openai_adapter.py` — OpenAIAdapter

Implements `LLMProvider` for the OpenAI API.

- **SDK path:** Uses `openai.AsyncOpenAI` if the `openai` package is installed
- **HTTP fallback:** Uses `aiohttp` directly if the SDK is unavailable
- `available()` — returns `True` if API key is set
- Default models: `{fast: gpt-4o-mini, strong: gpt-4o}`

### `src/llm/claude_adapter.py` — AnthropicAdapter + Compatible

**`AnthropicAdapter`:**
- Extracts system messages into the `system` parameter (Anthropic API style)
- Handles extended thinking models that return thinking+text content blocks via `_extract_text_from_content()`
- SDK path: `anthropic.AsyncAnthropic`, HTTP fallback via `aiohttp`
- Default models: `{fast: claude-sonnet-4-20250514, strong: claude-opus-4-20250514}`

**`AnthropicCompatibleAdapter`:**
- Inherits from `AnthropicAdapter`
- Requires both API key and base_url to be available
- Default models: `{fast: MiniMax-M2.7, strong: MiniMax-M2.7}`

### `src/llm/compatible_adapter.py` — OpenAICompatibleAdapter

Thin wrapper over `OpenAIAdapter` with a different `name` and availability check.

- Requires both API key and explicit base_url
- Works with: DeepSeek, Groq, Together, Ollama, vLLM, LiteLLM
- Default models: `{fast: default, strong: default}`

---

## Memory & Storage

### `src/memory/store.py` — MemoryStore

Two-layer storage analogous to CPU cache + disk:

**Hot layer:** `LRUCache` — in-memory `OrderedDict` with configurable max items (default 10,000). Methods: `get()`, `put()`, `invalidate()`, `clear()`.

**Cold layer:** SQLite with WAL mode, three tables:

#### `file_index` table
| Column | Type | Purpose |
|---|---|---|
| `path` | TEXT PK | Absolute file path |
| `hash` | TEXT | Content hash (SHA-256[:16]) |
| `size_bytes` | INTEGER | File size |
| `modified_at` | REAL | mtime |
| `indexed_at` | REAL | When indexed |
| `file_type` | TEXT | Extension |
| `summary` | TEXT | Structural summary from watcher |
| `metadata` | TEXT | JSON metadata |
| `priority` | INTEGER | P0/P1/P2 |
| `semantic_summary` | TEXT | LLM-generated summary |
| `last_accessed_at` | REAL | Access tracking |
| `triage_status` | TEXT | high/medium/low/skip |
| `embedding` | BLOB | Vector embedding bytes |
| `embedding_model` | TEXT | Model that produced embedding |

#### `knowledge` table
| Column | Type | Purpose |
|---|---|---|
| `id` | TEXT PK | Knowledge entry ID |
| `category` | TEXT | Category (behavior_insight, daily_report, user_profile, etc.) |
| `content` | TEXT | JSON content |
| `source_path` | TEXT | Origin path |
| `created_at` / `updated_at` | REAL | Timestamps |
| `metadata` | TEXT | JSON metadata |

#### `agent_context` table
| Column | Type | Purpose |
|---|---|---|
| `session_id` | TEXT PK | Session identifier |
| `agent_name` | TEXT | Which agent saved it |
| `context` | TEXT | JSON context data |
| `created_at` | REAL | Creation time |
| `expires_at` | REAL | TTL-based expiry |

**Schema migration:** `_migrate_schema()` adds v0.2+ columns to existing tables if they don't exist.

**Key query methods:**
- `search_files(query)` — vector search for 3+ word queries (if embeddings available), falls back to SQL LIKE
- `get_files_needing_summary(limit)` — prioritized by triage status (high → medium → untriaged), diversified across code/doc/image categories, excludes skip/low
- `get_untriaged_files(limit, type_priority=None)` — files with empty triage_status. When `type_priority` dict (ext → 1..10) is supplied, builds a dynamic `CASE file_type` SQL expression and orders by that first, then by `modified_at DESC`. Extension strings are validated against an allowlist pattern to prevent injection.
- `get_project_directories(min_files)` — finds directories containing marker files (pyproject.toml, package.json, etc.)
- `get_directory_breakdown(depth)` — groups all files by directory and category (code/doc/image/data/other)
- `vector_search(query_embedding, limit)` — brute-force cosine similarity scan over all embeddings

### `src/memory/embeddings.py` — Embedding Engine

Pluggable providers configured via `configure()`:

| Provider | Backend | Model | Dimensions |
|---|---|---|---|
| `local` | sentence-transformers | all-MiniLM-L6-v2 | 384 |
| `dashscope` | DashScope API (OpenAI-compatible) | qwen3-vl-embedding | configurable |
| `openai` | OpenAI API | text-embedding-3-small | configurable |

**`LocalEmbeddingProvider`:**
- Lazy-loads `SentenceTransformer` model
- Sets `HF_HUB_OFFLINE=1` to avoid network on load
- Batch encoding with `normalize_embeddings=True`

**`APIEmbeddingProvider`:**
- Uses `httpx` or falls back to `urllib.request`
- Batches API calls in chunks of 20
- Normalizes returned vectors with numpy

**Module-level functions:**
- `configure(provider, **kwargs)` — sets global `_provider_instance`
- `is_available()` → bool
- `embed_text(text)` → bytes | None
- `embed_texts(texts)` → list[bytes]
- `cosine_similarity(a_bytes, b_bytes)` → float
- `numpy_to_bytes(vec)` / `bytes_to_numpy(raw)` — serialization helpers

---

## Filesystem Watcher

### `src/filesystem/watcher.py` — FileSystemWatcher

Indexes configured paths into `MemoryStore`. Two modes:

**Full scan** (`_full_scan`):
- Walks all `watch_paths` with `os.walk()`
- Prunes ignored directories in-place via `_should_ignore_dir(dirname, parent)`:
  - `fnmatch` against `ignore_patterns` (single-name matching)
  - Substring check against `ignore_subpaths` on the full path (path-aware matching)
  - Hidden-dot directories directly under home are pruned unless whitelisted (currently only `.cursor` — specific noisy subtrees like `.cursor/extensions` are handled by `ignore_subpaths`)
- Skips files exceeding `max_file_size_mb`
- Only indexes files with configured extensions
- For binary types (.pdf, .docx, images): reads first 8KB, SHA-256[:16] hash, no text summary
- For text types: reads full content, computes content hash, extracts structural summary
- Cooperative yielding every 200 files (`await asyncio.sleep(0)`)
- Skips unchanged files (same mtime as indexed)

**Structural summary extraction** (`_extract_summary`):
- Python: counts imports, extracts class names and function names
- Markdown/Text: extracts headings
- JSON/YAML: line count
- All: first 30 lines preview (200 chars)

**Realtime watcher** (optional, requires `watchdog`):
- `_start_realtime_watcher()` creates a watchdog `Observer` with a custom `FileSystemEventHandler`
- Bridges watchdog's thread callbacks to the asyncio event loop via `asyncio.run_coroutine_threadsafe()`
- Handles `on_modified`, `on_created`, `on_deleted` events
- Applies the same ignore patterns and extension filters

**Periodic rescan:**
- Runs `_full_scan()` every `scan_interval_seconds` (default 300s)

**Stats tracked:**
- `files_indexed` — total count
- `last_scan` — timestamp (used by cron `after_scan` trigger)
- `scan_in_progress` — boolean
- `scan_progress_files` — running count during scan

---

## Scheduler

### `src/scheduler/pool.py` — AgentScheduler

Priority-based task scheduler with concurrency control — analogous to an OS process scheduler.

**Architecture:**
- `asyncio.PriorityQueue` with `(priority, created_at, task)` tuples
- `asyncio.Semaphore` for `max_concurrent_agents` (default 4)
- Background `_dispatch_loop` pulls from queue, creates `_run_task` coroutines
- Agent lookup via `_find_agent(task_name)` scanning `BUILTIN_AGENTS` + registered agents

**Task execution flow:**
1. `_dispatch_loop` pulls from priority queue
2. Acquires semaphore slot
3. Sets task state to RUNNING, records in `_active_tasks`
4. Emits `task.started` event
5. Calls `agent.execute(task, context)` with timeout
6. Sets result/error, state to COMPLETED/FAILED
7. Emits `task.completed` or `task.failed` event
8. Releases semaphore, signals done event

**API methods:**
- `submit(task, context)` — non-blocking enqueue
- `submit_and_wait(task, context)` — blocking with timeout (uses `asyncio.Event`)
- `fan_out(tasks, context)` — submit multiple in parallel, wait for all (like `Promise.all`)
- `register_agent(agent)` — add custom agents at runtime
- `get_task(task_id)` — lookup in active or completed (ring buffer, last 500)
- `status()` — queue size, active tasks, total dispatched, registered agents

---

## Syscall Interface

### `src/syscall/protocol.py` — Message Protocol

Transport-agnostic message format — the "ABI" between external agents and AgentOS.

**`SyscallType`** enum (19 operations):

| Category | Syscall | Maps to Agent |
|---|---|---|
| Files | `file.search`, `file.read`, `file.list`, `summarize.file` | file_search, file_read, file_list, summarizer |
| Knowledge | `knowledge.query`, `knowledge.store` | knowledge_query, knowledge_store |
| Context | `context.save`, `context.load` | context |
| Agents | `agent.submit`, `agent.task_status` | agent_submit, agent_task_status |
| Reports | `report.daily`, `report.project`, `report.brief` | report_generator |
| Profile | `profile.get` | profile_builder |
| Analysis | `analyze.behavior`, `classify.priority` | behavior_analyzer, priority_classifier |
| Triage | `triage.files` | triage |
| System | `sys.status`, `sys.ping` | system_status (ping is fast-pathed) |

**`SyscallRequest`** dataclass: `call_type`, `params`, `caller`, `request_id` (UUID hex[:12]), `timestamp`, `priority`

**`SyscallResponse`** dataclass: `request_id`, `success`, `data`, `error`, `elapsed_ms`, `timestamp`

Both have `to_json()` / `from_json()` for serialization.

### `src/syscall/server.py` — SyscallServer

**Unix socket server:**
- Listens at configured socket path (default `/tmp/agent_sys.sock`)
- Length-prefixed JSON protocol: 4 bytes big-endian length + JSON payload
- Handles multiple requests per connection
- 10MB safety limit per message

**HTTP server** (optional, via aiohttp):
- `POST /syscall` — JSON syscall dispatch
- `GET /status` — kernel status
- `GET /health` — health check
- `GET /events` — SSE event stream (proxies EventBus)
- `POST /reload` — hot-reload config

**Dispatch logic:**
- Fast-paths for `sys.ping` and `sys.status` (no scheduler involvement)
- All other syscalls: maps to agent name via `SYSCALL_TO_AGENT`, builds `AgentTask`, submits via `scheduler.submit_and_wait()`
- Special input mapping for context save/load and report subtypes

### `src/syscall/client.py` — Client SDK

**`SysAgentClient`** (async):
- Connects via `asyncio.open_unix_connection()`
- `_call()` — sends length-prefixed JSON, reads length-prefixed response
- High-level methods: `ping()`, `status()`, `file_search()`, `file_read()`, `knowledge_query()`, `knowledge_store()`, `context_save()`, `context_load()`, `report_daily()`, `report_project()`, `report_brief()`, `profile_get()`, `analyze_behavior()`, `classify_priority()`, `triage_files()`, `summarize_files()`
- Context manager support (`async with`)

**`SyncSysAgentClient`** (synchronous wrapper):
- Creates dedicated `asyncio.AbstractEventLoop`
- Wraps all async methods with `loop.run_until_complete()`
- For use in non-async environments (Cursor plugins, scripts)

---

## Web Dashboard

### `src/web/dashboard.py`

Standalone aiohttp web application with 20+ API endpoints. Reads directly from `~/.agent_sys/memory.db` — **works even when the daemon isn't running**.

**API endpoints:**

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Serve dashboard HTML SPA |
| `/static/{filename}` | GET | Serve JS/CSS assets |
| `/api/overview` | GET | File counts, summary progress, type/priority distribution, daemon PID |
| `/api/directories` | GET | Directory tree with content breakdown (code/doc/image/data/config/other) |
| `/api/knowledge` | GET | Browse knowledge entries by category |
| `/api/recent` | GET | Recently modified files with summaries |
| `/api/scheduling` | GET | Scheduling decision history |
| `/api/summary-progress` | GET | Summary completion by file type |
| `/api/search` | GET | File search (vector + keyword fallback) |
| `/api/triage` | GET | Triage distribution and directory breakdown |
| `/api/llm-config` | GET/POST | Read/write LLM provider configuration |
| `/api/llm-routing` | GET/POST | Per-function routing config |
| `/api/embedding-info` | GET | Current embedding provider info |
| `/api/embedding-config` | GET/POST | Embedding provider config |
| `/api/scheduling-strategy` | GET/POST | Cron strategy selection |
| `/api/daemon` | GET | Live daemon status (proxies to 127.0.0.1:7437) |
| `/api/events` | GET | SSE stream (in-process EventBus or daemon proxy) |
| `/api/trigger-agent` | POST | Manually submit agent task to daemon |
| `/api/reload-config` | POST | Trigger daemon config hot-reload |

**Dashboard tabs** (7):
1. Overview — file totals, summary progress, type distribution pie chart, priority bar chart
2. Files & Directories — search + directory composition stacked bars
3. LLM Config — visual provider/model/key editor
4. Triage — distribution chart + pipeline flow
5. Knowledge Base — category browser
6. Scheduling — decision history timeline
7. Summary Progress — per-type completion bars

---

## CLI

### `src/cli.py` — Entry Point

Registered as `agent-sys` console script via `pyproject.toml`.

**15 subcommands:**

| Command | Function | Description |
|---|---|---|
| `start` | `cmd_start` | Boot kernel (foreground or `-d` daemon mode). Refuses to start if another instance is alive; use `-f/--force` to kill-and-replace. Stale PID files (dead process) are cleaned automatically. |
| `stop` | `cmd_stop` | SIGTERM → wait → SIGKILL if needed |
| `status` | `cmd_status` | Query daemon via syscall client |
| `ping` | `cmd_ping` | Quick liveness check |
| `search <query>` | `cmd_search` | Search indexed files (optional `--type` filter) |
| `query` | `cmd_query` | Browse knowledge base (optional `--category`) |
| `report <type>` | `cmd_report` | Generate daily/project/brief report |
| `profile` | `cmd_profile` | Show user profile |
| `summarize` | `cmd_summarize` | Run LLM summarization |
| `analyze` | `cmd_analyze` | Run behavior analysis (optional `--hours`) |
| `classify` | `cmd_classify` | Run priority classification |
| `triage` | `cmd_triage` | Run LLM file importance triage |
| `dashboard` | `cmd_dashboard` | Launch web dashboard (optional `--port`) |

**Start sequence:**
1. Load config (from `--config` path or default)
2. Check existing PID file:
   - If process alive + `--force` → SIGTERM → wait 5s → SIGKILL if still alive → continue
   - If process alive + no `--force` → print status/stop/force hints and exit 1
   - If stale (process dead) → remove PID file and continue
3. Check LLM availability → interactive setup prompt if none (Ollama / Remote API / Skip)
4. Optionally daemonize (Unix double-fork)
5. Print banner with PID, socket, HTTP port
6. `asyncio.run(kernel.run())`

**LLM setup wizard** (`_check_llm_and_prompt`):
- Loads saved config from `~/.agent_sys/llm_config.yaml`
- If no providers available, presents interactive menu:
  - [1] Local Ollama — configures openai_compatible adapter
  - [2] Remote API — OpenAI / OpenAI-Compatible / Anthropic / Anthropic-Compatible
  - [3] Skip — rule-based mode only
- Saves choice to `~/.agent_sys/llm_config.yaml` (mode 0600)

---

## File Extractors

### `src/extractors.py`

Content extraction for non-plaintext file types, used by the summarizer.

**Type detection:**
- `is_image(ext)` — .png, .jpg, .jpeg, .gif, .webp, .bmp
- `is_document(ext)` — .pdf, .docx, .doc, .pptx, .xlsx
- `is_plaintext(ext)` — 30 extensions covering code, config, and text files

**Extractors:**
- `extract_text_from_pdf(path)` — tries PyMuPDF first, falls back to PyPDF2, returns first 4000 chars
- `extract_text_from_docx(path)` — uses python-docx, returns first 4000 chars
- `encode_image_base64(path)` — returns `data:{mime};base64,...` URI (max 5MB)

**Unified API:**
```python
extract_content(path) -> {"type": "text", "content": "..."} | {"type": "image", "data_uri": "..."} | None
```
- PDF: tries text extraction, falls through to image encoding for scanned PDFs
- DOCX: text extraction only
- PPTX/XLSX: returns None (unsupported)
- Images: base64 data URI
- Plaintext: reads first 4000 chars

---

## Data Flow

### Startup → First Indexing

```
agent-sys start
    → SysAgentKernel.boot()
        → MemoryStore.initialize() (SQLite + schema migration)
        → FileSystemWatcher.start() (background initial scan)
            → os.walk(~) → upsert_file() for each indexed file
            → _start_realtime_watcher() (watchdog if available)
        → CronScheduler.start()
            → after_scan trigger watches filesystem._stats["last_scan"]
```

### Triage → Summarize Pipeline

```
[CronScheduler after_scan] → dispatch("triage")
    → TriageAgent.execute()
        → Phase 1: rule-based skip (site-packages, node_modules, etc.)
        → Phase 2: LLM batch classify (high/medium/low/skip)
    → [CronScheduler after:triage] → dispatch("summarizer")
        → SummarizerAgent.execute()
            → get_files_needing_summary() (high → medium → untriaged, skip/low excluded)
            → For each file: read content → LLM summarize → update_semantic_summary()
            → Build hierarchical project summaries
            → Compute embeddings for summarized files
```

### External Agent Query

```
Cursor/Claude Code
    → SysAgentClient.file_search("database migration")
        → Unix socket → SyscallServer._dispatch()
            → AgentTask(name="file_search") → scheduler.submit_and_wait()
                → FileSearchAgent.execute()
                    → memory.search_files() (vector search or SQL LIKE)
                → SyscallResponse → client
```

### Adaptive Scheduling Cycle

```
CronScheduler._adaptive_loop()
    → _gather_activity_snapshot() (mod rates, triage stats, summary progress, LLM status)
    → _llm_decide_schedule() (or _default_decision for no-LLM)
    → For each agent in decision:
        → Check cooldown + already-running
        → _dispatch(agent_name, input_data)
    → _persist_decision() to knowledge DB
    → Sleep for LLM-recommended interval
```

---

## Dependencies

### Required
- `pyyaml>=6.0` — config loading

### Optional (full install)
- `watchdog>=4.0` — realtime filesystem events
- `aiohttp>=3.9` — HTTP API + dashboard
- `openai>=1.0` — OpenAI provider
- `anthropic>=0.30` — Anthropic provider

### Optional (document extraction)
- `PyMuPDF>=1.24` — PDF text extraction (imported as `fitz`)
- `python-docx>=1.1` — Word .docx extraction

### Optional (embeddings)
- `sentence-transformers` — local embedding model
- `numpy` — vector operations
- `httpx` — API embedding provider HTTP client

### Installation

```bash
pip install -e ".[full]"          # All providers + HTTP + watchdog
pip install -e ".[lite]"          # watchdog + HTTP only
pip install PyMuPDF python-docx   # Document extraction
```
