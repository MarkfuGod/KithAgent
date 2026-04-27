# KithAgent Backend Codebase Index

> Local note for future Cursor sessions. This file is intentionally ignored by git.

## What This Backend Is

`agent-sys` is a local Python daemon that indexes user-approved files, stores metadata and knowledge in SQLite, runs LLM-powered agents, and exposes those capabilities through a syscall-style RPC layer.

It is not primarily a web app. The durable backend shape is:

```text
CLI / Desktop / Dashboard / External Agents
        -> Syscall RPC
        -> AgentScheduler
        -> Built-in Agents
        -> MemoryStore + LLM Router + FileSystemWatcher
        -> ~/.agent_sys/memory.db
```

## Runtime Data

User-specific runtime state lives under `~/.agent_sys/`:

- `memory.db`: SQLite store for indexed files, knowledge, context, profile facts.
- `auth_token`: HTTP syscall token.
- `logs/sysagent.log`: daemon logs.
- `llm_config.yaml`: persisted user model settings.
- `scan_config.yaml`: persisted user scan scope.

Do not commit runtime state, API keys, tokens, local DBs, or logs.

## Entry Points

### CLI

Main entry:

- `src/cli.py`

Package script:

- `pyproject.toml` maps `agent-sys = "src.cli:main"`.

Important commands:

- `agent-sys start`
- `agent-sys start -d`
- `agent-sys stop`
- `agent-sys status`
- `agent-sys ping`
- `agent-sys search`
- `agent-sys query`
- `agent-sys profile`
- `agent-sys report`
- `agent-sys triage`
- `agent-sys summarize`
- `agent-sys analyze`
- `agent-sys classify`
- `agent-sys dashboard`

Most non-start commands use `src/syscall/client.py` to talk to the running daemon over the Unix socket.

### Desktop App

Electron desktop code lives in:

- `desktop/src/main/main.js`
- `desktop/src/preload/preload.js`
- `desktop/src/renderer/src/App.tsx`

The intended security boundary is:

- Renderer UI never reads tokens or API keys.
- Main process owns daemon control and privileged local calls.
- Preload exposes a narrow `window.kith` API.

## Boot Chain

The daemon start flow is:

1. `src/cli.py::cmd_start`
2. `src/kernel/config.py::load_config`
3. saved scan/model settings are merged from `~/.agent_sys/*_config.yaml`
4. optional first-run prompts when not daemonized
5. optional `_daemonize()` for `-d`
6. `src/kernel/daemon.py::SysAgentKernel.run`
7. `SysAgentKernel.boot`

`SysAgentKernel.boot` initializes subsystems in this order:

1. `EventBus`
2. `MemoryStore`
3. embedding provider
4. `ModelRouter`
5. `FileSystemWatcher`
6. `AgentScheduler`
7. `CronScheduler`
8. `SyscallServer`

Kernel accessors used by syscall handlers:

- `get_memory()`
- `get_filesystem()`
- `get_scheduler()`
- `get_llm()`
- `get_event_bus()`

## Configuration

Default config:

- `config/default.yaml`

Config loader:

- `src/kernel/config.py`

Important dataclasses:

- `KernelConfig`
- `FilesystemConfig`
- `MemoryConfig`
- `SchedulerConfig`
- `SyscallConfig`
- `LLMConfig`
- `CronConfig`
- `TriageConfig`
- `AgentOSConfig`

User-facing settings helpers:

- `src/kernel/user_settings.py`

Key helpers:

- `normalize_watch_paths`
- `load_scan_settings`
- `save_scan_settings`
- `save_model_settings`

## Kernel Modules

### `src/kernel/daemon.py`

Owns daemon lifecycle:

- creates runtime dirs
- writes/removes PID file
- installs signal handlers
- boots subsystems
- shuts subsystems down in reverse order
- hot-reloads LLM and embedding config via `reload_config`
- reports daemon status through `status()`

### `src/kernel/event_bus.py`

In-process pub/sub for observability:

- task started/completed/failed events
- LLM request/response events
- config reload events
- SSE-friendly event serialization

### `src/kernel/cron.py`

Background scheduling policy:

- fixed cron jobs
- after-scan / after-agent triggers
- adaptive LLM scheduling with rule fallback
- submits `AgentTask`s into `AgentScheduler`

### `src/kernel/user_settings.py`

Shared settings layer for CLI, daemon, and desktop:

- scan path persistence
- model mode persistence
- Ollama / API / local-mode model config

## Filesystem Indexing

Main file:

- `src/filesystem/watcher.py`

Responsibilities:

- full scan over configured `watch_paths`
- periodic rescan
- optional watchdog realtime events
- ignore generated/cache/sensitive paths
- compute file hash and lightweight summary
- write file rows through `MemoryStore.upsert_file`

Important config:

- `filesystem.watch_paths`
- `filesystem.ignore_patterns`
- `filesystem.allowed_hidden_home_dirs`
- `filesystem.index_extensions`
- `filesystem.max_file_size_mb`

Startup note:

- A long initial scan can delay perceived daemon readiness if syscall/HTTP startup waits behind scanning.
- For debugging startup, check `~/.agent_sys/logs/sysagent.log` and whether `/tmp/agent_sys.sock` exists.

## Memory / Storage

Main file:

- `src/memory/store.py`

Main class:

- `MemoryStore`

Primary tables:

- `file_index`: indexed files, summaries, triage status, priority, embeddings.
- `knowledge`: reports, profiles, behavior insights, scheduling decisions.
- `agent_context`: short-lived cross-session context with TTL.
- `profile_facts`: user-correctable facts inferred from profile generation.
- `source_records`: provenance records for browser domains, bookmarks, downloads, recent files, and folder activity.
- `insight_runs`: First Insight run timing, input counts, status, and output summary.
- `insight_items`: correctable topics/suggestions emitted by insight runs.

Important methods:

- `initialize`
- `upsert_file`
- `get_file_info`
- `search_files`
- `vector_search`
- `get_files_needing_summary`
- `update_semantic_summary`
- `get_recently_modified_files`
- `get_directory_activity`
- `get_project_directories`
- `get_directory_breakdown`
- `store_knowledge`
- `query_knowledge`
- `upsert_profile_fact`
- `list_profile_facts`
- `update_profile_fact_status`
- `save_context`
- `load_context`
- `stats`
- `prune_out_of_scope`

Embedding layer:

- `src/memory/embeddings.py`

## Scheduler

Main file:

- `src/scheduler/pool.py`

Main class:

- `AgentScheduler`

Responsibilities:

- priority queue
- concurrency limit with `asyncio.Semaphore`
- task lifecycle tracking
- agent registry
- synchronous syscall-style `submit_and_wait`
- fan-out execution for agents that spawn child tasks

Agents are loaded from:

- `src/agents/builtin.py::BUILTIN_AGENTS`

Every task receives a shared context containing:

- `memory`
- `scheduler`
- `filesystem`
- `kernel`
- `llm`
- `event_bus`

## Syscall RPC

Protocol files:

- `src/syscall/protocol.py`
- `src/syscall/server.py`
- `src/syscall/client.py`

### Protocol

`src/syscall/protocol.py` defines:

- `SyscallType`
- `SYSCALL_TO_AGENT`
- `SyscallRequest`
- `SyscallResponse`

### Server

`src/syscall/server.py` supports:

- Unix socket transport with 4-byte length-prefixed JSON
- optional HTTP transport if `aiohttp` is installed
- `/syscall`
- `/status`
- `/health`
- `/events`
- `/reload`

Important behavior:

- `sys.ping` and `sys.status` are fast-path public syscalls.
- Other syscalls map to agent names through `SYSCALL_TO_AGENT`.
- `_build_input` normalizes report and assistant subtypes into agent input.
- HTTP calls require `X-Agent-Token` when `require_auth` is true.
- Unix socket is local and mode `0600`; anonymous Unix calls are allowed by default.

### Client

`src/syscall/client.py` provides:

- `SysAgentClient`
- `SyncSysAgentClient`

Default client transport is Unix socket:

- `/tmp/agent_sys.sock`

## Built-in Agents

Base abstractions:

- `src/agents/base.py`

Registry:

- `src/agents/builtin.py`

Core agents:

- `file_search`
- `file_read`
- `file_list`
- `knowledge_query`
- `knowledge_store`
- `context`
- `system_status`
- `summarizer`
- `behavior_analyzer`
- `priority_classifier`
- `report_generator`
- `profile_builder`
- `triage`
- `agent_submit`
- `agent_task_status`
- `assistant`

Feature files:

- `src/agents/triage.py`: classify files as high/medium/low/skip.
- `src/agents/summarizer.py`: summarize files and projects.
- `src/agents/analyzer.py`: infer behavior and activity patterns.
- `src/agents/prioritizer.py`: hot/warm/cold priority classification.
- `src/agents/reporter.py`: daily, project, brief reports.
- `src/agents/profile_builder.py`: whole-person profile generation.
- `src/agents/assistant.py`: consumer-facing Jarvis facade.

## Jarvis / Consumer Surface

New product-oriented syscall types live in:

- `src/syscall/protocol.py`

Current consumer syscalls:

- `assistant.chat`
- `assistant.first_insight`
- `onboarding.bootstrap`
- `profile.summary`
- `memory.review`
- `memory.feedback`
- `sources.get`
- `sources.configure`
- `settings.model`

Main implementation:

- `src/agents/assistant.py`

The assistant agent composes:

- confirmed profile facts
- inferred profile facts
- First Insight runs and product suggestions
- latest `user_profile`
- recent `behavior_insight`
- recent `context_brief`
- recent file activity
- LLM response if an available provider exists
- conservative fallback answer if no LLM is configured

Profile facts are generated by:

- `src/agents/profile_builder.py::_store_correctable_facts`

Profile fact statuses:

- `inferred`
- `confirmed`
- `rejected`
- `hidden`

### First Insight

Main files:

- `src/agents/assistant.py`
- `src/ingest/browser_history.py`
- `src/memory/store.py`

The first-run product path is `assistant.first_insight` (alias `onboarding.bootstrap`). It is intentionally lighter than the background indexing pipeline:

```text
answers + recent file metadata + folder activity
  + optional Chromium History/Bookmarks/Downloads metadata
  -> assistant onboarding bootstrap
  -> user_profile + profile_facts
  -> source_records + insight_runs + insight_items
```

Browser ingestion reads low-risk metadata only. It copies live `History` SQLite files before querying, parses `Bookmarks` JSON, and can read Downloads metadata from the History DB. It does not read Cookies, Login Data, sessions, passwords, or tokens, and sanitized URLs drop query strings and fragments.

The legacy dashboard remains diagnostics-first. Consumer UI should render First Insight as: initial profile, recent themes, actionable suggestions, source provenance, and correction controls.

### First-Run Layering

Fast startup is a product requirement. Do not block first insight or basic UI readiness on LLM triage, summarization, embeddings, or full semantic analysis. The backend should be treated as three layers:

```text
Layer 1: Fast Index
  file names + paths + file types + size + mtime
  recent active directories
  Chromium title/domain/bookmark/download metadata
  -> first usable profile within minutes

Layer 2: Exact Retrieval
  grep / SQLite FTS5 over text content and lightweight summaries
  -> answer "where did I write/read X?" style questions

Layer 3: Semantic Understanding
  hard filters -> soft preference filters -> LLM triage
  high/medium file summaries -> embeddings -> profile/preference extraction
  -> improves quality in the background
```

Design rule: Layer 1 must run without cloud calls and without reading large file bodies. Layer 2 should be deterministic and precise. Layer 3 can be slower, cancellable, and incremental; it must never recursively summarize its own summary artifacts.

## Current Pipeline State

This section records the latest local implementation state from the summary-pipeline optimization work.

### Filtering And Triage

- Hard filters now live in `config/default.yaml -> triage` and `src/kernel/config.py::TriageConfig`.
- `triage.skip_path_patterns` handles noisy path substrings such as `node_modules`, `vendor`, `dist`, `build`, `generated`, package caches, IDE extensions, and similar third-party/cache trees.
- `triage.hard_skip_extensions` and `triage.hard_skip_file_patterns` are deterministic filters for lockfiles, source maps, minified/bundled files, generated files, protobuf outputs, etc.
- `src/agents/triage.py` applies hard filters before LLM triage and emits `triage.batch_progress` / `triage.batch_failed`.
- `unknown` means “parked until a real LLM triage run can classify it”; it is not eligible for default summarization.

### Summarization

- `src/memory/store.py::get_files_needing_summary` now defaults to `triage_status IN ('high', 'medium')`.
- Untriaged fallback exists only through an explicit `include_untriaged=True` parameter.
- `src/agents/summarizer.py` runs deep summarization with bounded concurrency (`max_concurrency`, default 4, capped at 8).
- Summary progress events include path, type, triage status, concurrency, summarized count, errors, and preview.
- File summaries are stored on `file_index.semantic_summary`; project summaries stay in `knowledge.category='project_summary'`.
- Summary artifacts should not be recursively summarized as input files.

### File Cluster Recommendations

- `src/web/api/clusters.py` provides:
  - `GET /api/file-clusters`
  - `POST /api/file-clusters/decision`
- The dashboard `Triage` tab shows “File Cluster Recommendations”.
- Users can mark a cluster `Include` (`high`) or `Exclude` (`skip`) before spending LLM summarization tokens.
- This is intended for non-developer machines where large folders should be approved or excluded by the user, not silently summarized.

### Dashboard Observability

- `src/llm/router.py` emits `llm.request`, `llm.response`, and `llm.error` with a `call_id`.
- LLM events include provider/model, task type, token usage, latency, prompt preview, response preview/content, and truncation metadata.
- `src/web/static/events.js` groups LLM calls by `call_id`, folds classify JSON by default, and shows a task timeline.
- Behavior analysis now emits `behavior_insight.started`, `behavior_insight.completed`, and `behavior_insight.failed`.
- The live dashboard is still diagnostics-first; the Electron UI should remain the consumer surface.

### Behavior Insight

- `src/agents/analyzer.py` now parses LLM JSON leniently, including fenced JSON or JSON wrapped in prose.
- On parse/model failure it returns a structured fallback with `error` and `raw` preview instead of silently producing unusable state.
- Dashboard events make analyzer failures visible instead of burying them in generic LLM logs.

### LLM And Embeddings

- Runtime model config lives in `~/.agent_sys/llm_config.yaml` and should not be committed.
- Current intended chat provider is DashScope through `openai_compatible`:
  - base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
  - text model: `qwen3.6-plus`
  - vision model: `qwen-vl-plus`
- Current intended embedding provider is DashScope text embeddings:
  - env var: `DASHSCOPE_API_KEY`
  - model: `text-embedding-v4`
  - dimensions: `1024`
- Do not put API keys in `config/default.yaml`; that file should contain env var names only.
- `src/memory/embeddings.py` uses batch size 10 for DashScope/OpenAI-compatible embedding calls because DashScope text embeddings cap batches at 10.
- Existing local DB state after the latest run had roughly 57k indexed files, dozens of summaries, and embeddings computed for current high/medium summaries. Treat those numbers as local runtime state, not product constants.

### RAG Status

- Full hybrid RAG now has a chunk-level path in addition to legacy summary search.
- `src/memory/store.py` owns `document_chunks` plus `document_chunks_fts` (SQLite FTS5 when available), chunk embedding updates, FTS search, vector chunk search, and hybrid score fusion.
- `src/memory/chunking.py` splits extracted text into deterministic overlapping chunks with line ranges for citations.
- `src/agents/rag_indexer.py` runs as a delayed, low-priority background agent. Defaults in `config/default.yaml -> memory.rag` wait 600 seconds after boot/First Insight before indexing, so startup and onboarding stay fast.
- `src/kernel/cron.py` appends `rag_indexer` to adaptive scheduling only after the delay, when there are pending eligible high/medium files, and no recent RAG run is active.
- `src/agents/assistant.py` calls `MemoryStore.hybrid_search_chunks()` best-effort during chat. If RAG is unavailable, empty, or slow, it falls back to the existing profile/facts/recent-files context packet.
- Assistant responses can return `sources` with source IDs, paths, line ranges, snippets, scores, and retrieval modes. The prompt asks the model to cite retrieved evidence as `[S1]`, `[S2]`, etc.
- Legacy file-level semantic search remains: `src/memory/store.py::search_files` and `src/web/api/search.py` still try vector search over `file_index.semantic_summary` and fall back to SQL LIKE.

## LLM Layer

Main files:

- `src/llm/base.py`
- `src/llm/router.py`
- `src/llm/openai_adapter.py`
- `src/llm/claude_adapter.py`
- `src/llm/compatible_adapter.py`

Important classes:

- `LLMMessage`
- `LLMResponse`
- `LLMProvider`
- `ModelRouter`

Router behavior:

- routes by `task_type`
- supports per-function config
- supports text/vision provider split
- emits LLM events to `EventBus`
- tracks basic circuit-breaker state for provider auth failures

Provider keys and model settings are usually read from:

- `config/default.yaml`
- `~/.agent_sys/llm_config.yaml`
- environment variables

## Web Dashboard

Main files:

- `src/web/dashboard.py`
- `src/web/dashboard.html`
- `src/web/api/*.py`
- `src/web/static/*`

Dashboard API helpers:

- `src/web/_utils.py`

Important constants:

- `DB_PATH = ~/.agent_sys/memory.db`
- `LLM_CONFIG_PATH = ~/.agent_sys/llm_config.yaml`
- `AUTH_TOKEN_PATH = ~/.agent_sys/auth_token`
- `DAEMON_HTTP_PORT = 7437`

Dashboard APIs include:

- `/api/overview`
- `/api/directories`
- `/api/recent`
- `/api/summary-progress`
- `/api/search`
- `/api/triage`
- `/api/triage/skipped-directories`
- `/api/knowledge`
- `/api/scheduling`
- `/api/llm-config`
- `/api/llm-routing`
- `/api/embedding-info`
- `/api/embedding-config`
- `/api/scheduling-strategy`
- `/api/rag/status`
- `/api/rag/config`
- `/api/rag/trigger`
- `/api/rag/debug-search`
- `/api/rag/logs`
- `/api/daemon`
- `/api/trigger-agent`
- `/api/reload-config`
- `/api/events`

The dashboard is still mostly a developer/diagnostics surface. The Electron UI should be the consumer surface.

## Data Flows

### File Indexing

```text
watch_paths
  -> FileSystemWatcher
  -> MemoryStore.upsert_file
  -> file_index
  -> search / triage / summarizer / profile builder
```

### External Request

```text
Client
  -> SyscallRequest
  -> SyscallServer._dispatch
  -> AgentTask
  -> AgentScheduler.submit_and_wait
  -> BaseAgent.execute
  -> SyscallResponse
```

### Background Intelligence

```text
CronScheduler
  -> AgentScheduler
  -> triage / summarizer / analyzer / reporter / profile_builder / rag_indexer
  -> MemoryStore
  -> knowledge + profile_facts + document_chunks + chunk embeddings
```

### Desktop Jarvis

```text
Electron renderer
  -> preload window.kith
  -> Electron main
  -> local daemon syscall
  -> assistant agent
  -> profile facts + knowledge + recent files + hybrid RAG evidence + LLM router
```

### Hybrid RAG

```text
watch_paths / file_index
  -> triage high|medium
  -> delayed rag_indexer
  -> extract_content
  -> chunk_text
  -> document_chunks + document_chunks_fts
  -> chunk embeddings
  -> AssistantAgent._chat hybrid_search_chunks
  -> cited source snippets
```

## Debugging Checklist

### Daemon Will Not Start

Check:

- `~/.agent_sys/logs/sysagent.log`
- `/tmp/agent_sys.pid`
- `/tmp/agent_sys.sock`
- `agent-sys status`
- `agent-sys ping`

If using HTTP:

- verify `aiohttp` is installed
- probe `http://127.0.0.1:7437/health`
- probe `http://127.0.0.1:7437/status`

If `aiohttp` is missing, Unix socket CLI calls can still work, but HTTP-based integrations will fail.

### Desktop Cannot Reach Daemon

Look at:

- `desktop/src/main/main.js`
- whether it expects HTTP or Unix socket
- whether `python3 -m src.cli start -d` exits successfully
- `~/.agent_sys/logs/sysagent.log`

Known current symptom:

- Electron showed `agent-sys daemon did not become ready`.
- Local probe showed `aiohttp missing No module named 'aiohttp'`.
- That means HTTP status polling on port `7437` cannot succeed unless `aiohttp` is installed or Electron switches to Unix socket transport.

### Syscall Fails

Check:

- call type exists in `SyscallType`
- `SYSCALL_TO_AGENT` maps it to the intended agent
- target agent exists in `BUILTIN_AGENTS`
- `BaseAgent.name` matches the mapping
- caller is allowed in `config/default.yaml -> syscall.allowed_callers`
- HTTP has valid `X-Agent-Token` if using HTTP

### Agent Runs But Returns Empty Data

Check:

- `MemoryStore.stats()`
- whether `file_index` has rows
- whether scan scope is too narrow
- whether triage marked everything `skip`, `low`, or `unknown`
- whether LLM provider is available through `ModelRouter.available_providers()`

## Frontend Codebase Index

There are currently two frontend surfaces:

1. Electron desktop app under `desktop/`
2. legacy aiohttp dashboard under `src/web/`

The Electron app is the consumer Jarvis surface. The legacy dashboard should remain Advanced/Diagnostics.

### Electron Package

Main files:

```text
desktop/package.json
desktop/vite.config.ts
desktop/tsconfig.json
desktop/src/main/main.js
desktop/src/preload/preload.js
desktop/src/renderer/index.html
desktop/src/renderer/vite-env.d.ts
desktop/src/renderer/src/main.tsx
desktop/src/renderer/src/App.tsx
desktop/src/renderer/src/styles.css
```

Scripts:

```bash
cd desktop && npm run dev
cd desktop && npm run typecheck
cd desktop && npm run build
cd desktop && npm run start
```

Current package shape:

- Electron main is plain ESM JavaScript.
- Renderer is React + TypeScript + Vite.
- There is no route library yet; `App.tsx` owns tab state.
- CSS is a single global stylesheet.

### Electron Main Process

Main file:

- `desktop/src/main/main.js`

Responsibilities:

- create the macOS-style `BrowserWindow`
- decide dev vs packaged renderer URL
- spawn Python daemon with `python3 -m src.cli start -d`
- stop daemon through `python3 -m src.cli stop`
- read `~/.agent_sys/auth_token`
- call daemon syscalls
- open legacy dashboard in browser
- register IPC handlers

IPC handlers:

```text
daemon:status
daemon:start
daemon:stop
daemon:openDashboard
jarvis:chat
profile:summary
memory:review
memory:feedback
sources:get
sources:configure
settings:model
```

Current important risk:

- `main.js` uses HTTP polling and HTTP syscall calls at `http://127.0.0.1:7437`.
- This requires daemon HTTP support, which requires Python `aiohttp`.
- If `aiohttp` is missing, CLI/Unix socket can still work, but Electron currently reports `agent-sys daemon did not become ready`.
- Preferred future fix: use the Unix socket protocol in Electron main, or guarantee `aiohttp` in install/runtime packaging.

### Preload Bridge

Main file:

- `desktop/src/preload/preload.js`

Responsibilities:

- expose a narrow `window.kith` API through `contextBridge`
- keep renderer isolated from Node/Electron APIs
- prevent renderer from directly reading auth tokens, filesystem, or environment variables

Exposed API groups:

```ts
window.kith.daemon.status()
window.kith.daemon.start()
window.kith.daemon.stop()
window.kith.daemon.openDashboard()
window.kith.jarvis.chat(payload)
window.kith.profile.summary(payload)
window.kith.memory.review(payload)
window.kith.memory.feedback(payload)
window.kith.sources.get()
window.kith.sources.configure(payload)
window.kith.settings.model(payload)
```

Frontend rule:

- Renderer should only call `window.kith`.
- Do not import `electron`, `node:fs`, `node:child_process`, or token paths in renderer code.

### Renderer App

Entry files:

- `desktop/src/renderer/index.html`
- `desktop/src/renderer/src/main.tsx`
- `desktop/src/renderer/src/App.tsx`
- `desktop/src/renderer/vite-env.d.ts`

`main.tsx` mounts React:

```text
createRoot(document.getElementById('root')).render(<App />)
```

`vite-env.d.ts` defines:

- `DaemonStatus`
- `ChatMessage`
- `ProfileFact`
- `ProfileSummary`
- `MemoryReview`
- `SourceSettings`
- `KithApi`
- `window.kith`

`App.tsx` is currently a single-file MVP app.

State owned by `App`:

- active tab
- daemon status
- chat messages
- current draft message
- profile summary
- memory review list
- source settings
- source textarea draft
- model mode and model settings
- busy/notice UI state

Primary actions in `App.tsx`:

- `refreshAll`
- `startDaemon`
- `submitChat`
- `generateProfile`
- `updateFact`
- `saveSources`
- `saveModel`

Tabs:

```text
Ask Jarvis
About Me
Memories
Sources & Privacy
Advanced
```

What each tab does:

- `Ask Jarvis`: chat surface backed by `assistant.chat`.
- `About Me`: shows raw-ish profile JSON and correctable facts from `profile.summary`.
- `Memories`: reviews `profile_facts`, supports accurate / inaccurate / hidden feedback.
- `Sources & Privacy`: edits scan paths and model mode.
- `Advanced`: shows daemon status and links to old dashboard / stop daemon.

### Renderer Styling

Main file:

- `desktop/src/renderer/src/styles.css`

Design direction:

- dark Mac desktop aesthetic
- glass panels
- warm gold + mint accent colors
- large sidebar + content layout
- no developer-console layout in main tabs

Important CSS concepts:

- `-webkit-app-region: drag` on sidebar for frameless macOS window feel
- `.orb` as brand/Jarvis visual anchor
- `.panel`, `.hero-card`, `.daemon-card` for glass cards
- `.message.user` / `.message.assistant` for chat bubbles
- `.fact` / `.fact-actions` for memory review

Current limitations:

- not responsive below 980px
- no accessibility audit yet
- no route separation or component split yet
- no streaming chat UI yet
- no empty/error state polish beyond basic notices

### Legacy Dashboard Frontend

Main files:

```text
src/web/dashboard.html
src/web/static/common.js
src/web/static/app.js
src/web/static/overview.js
src/web/static/files.js
src/web/static/knowledge.js
src/web/static/scheduling.js
src/web/static/summary.js
src/web/static/triage.js
src/web/static/events.js
src/web/static/llm-config.js
src/web/static/routing-ui.js
src/web/static/dashboard.css
```

Role:

- internal dashboard / diagnostics
- useful for inspecting indexing, scheduling, LLM config, events, triage, knowledge
- not suitable as the consumer Jarvis UI without heavy simplification

Developer-heavy surfaces to keep out of consumer UI:

- PID/socket/token details
- raw SSE stream
- manual agent triggers
- LLM routing tables
- triage token/cost language
- database-like knowledge rows

### Frontend Data Flow

```text
Renderer App
  -> window.kith
  -> preload ipcRenderer.invoke
  -> Electron main ipcMain.handle
  -> daemon syscall
  -> assistant/profile/memory/sources/settings backend
```

Consumer UI should never call daemon HTTP directly. Main process owns transport choice.

### Frontend Refactor Targets

When the MVP works, split `App.tsx` into:

```text
desktop/src/renderer/src/App.tsx
desktop/src/renderer/src/api/kith.ts
desktop/src/renderer/src/components/Shell.tsx
desktop/src/renderer/src/components/DaemonStatus.tsx
desktop/src/renderer/src/features/chat/ChatView.tsx
desktop/src/renderer/src/features/profile/ProfileView.tsx
desktop/src/renderer/src/features/memory/MemoryView.tsx
desktop/src/renderer/src/features/privacy/PrivacyView.tsx
desktop/src/renderer/src/features/advanced/AdvancedView.tsx
```

Also add:

- dedicated loading states per tab
- better error recovery around daemon startup
- streamed assistant responses
- first-run onboarding flow
- source directory picker using native dialog
- model setup wizard
- Keychain-backed API key storage
- accessibility pass
- app packaging/signing/notarization plan

## Backend Work Needed For A Real Jarvis Product

To turn this into a convincing Jarvis product, the backend needs to become reliable, explainable, privacy-aware, and measurable. The main work is below.

### 1. Reliable Local Runtime

Must-have work:

- single-instance daemon supervision
- deterministic startup readiness
- no blocking initial scan before RPC readiness
- robust Unix socket desktop integration
- optional HTTP only as a secondary transport
- crash recovery and log rotation
- clear daemon health model
- install-time dependency management

Minimum health signals:

- daemon running
- syscall reachable
- scheduler running
- memory DB reachable
- scan status
- LLM provider status
- queue length
- last successful task time

### 2. Privacy And Consent System

Must-have work:

- explicit scan scopes
- source-level enable/disable
- sensitive-path defaults
- per-file or per-folder exclusion
- before-cloud-send policy
- local-only mode
- memory export/delete
- user-correctable inferred facts
- audit trail for “why do you know this?”

Jarvis must always answer:

- what data was used
- where it came from
- whether it is confirmed or inferred
- how to delete or correct it

### 3. Memory Architecture

Current memory is file/knowledge centric. A Jarvis product needs multiple memory types:

- semantic memory: durable facts about user
- episodic memory: events and recent activity over time
- preference memory: explicit user preferences
- working memory: current session/context
- source memory: indexed file/document/image summaries
- feedback memory: corrections and rejections

Backend work:

- versioned profile model
- confidence and provenance for every fact
- contradiction handling
- memory decay / stale fact detection
- deduplication
- semantic search over knowledge entries, not only files
- user feedback loop that changes future answers

### 4. Personal Understanding Pipeline

Pipeline should be explicit:

```text
scan sources
  -> fast metadata index (first-run profile path)
  -> exact text index (FTS/grep path)
  -> classify sensitivity
  -> hard filter + user cluster decisions
  -> soft preference filter
  -> LLM triage importance
  -> summarize high/medium content
  -> extract candidate facts/preferences/events
  -> deduplicate and score confidence
  -> ask user to confirm high-impact claims
  -> use confirmed memory in answers
```

Important agents to add or strengthen:

- sensitivity classifier
- fact extractor
- preference extractor
- memory consolidator
- contradiction detector
- profile versioner
- reflection/report agent
- user feedback learner

### 5. Retrieval Quality

Jarvis fails if it cannot find the right context quickly.

Backend needs:

- exact retrieval first: SQLite FTS5 / grep-like search over text content,
  file names, paths, and lightweight summaries for questions like “where did I
  write/read X?”
- semantic retrieval second: vector search over summaries/chunks after
  background summarization and embeddings have completed
- hybrid retrieval: FTS/keyword + vector + recency + source priority
- query rewriting for personal questions
- category-aware retrieval
- citation/source tracking
- answer grounding
- “insufficient evidence” behavior
- evaluation queries with expected sources

Current RAG state:

- `src/memory/embeddings.py` supports local, DashScope, and OpenAI embedding providers.
- `src/memory/store.py::search_files` and `src/web/api/search.py` try vector search for natural-language queries and fall back to SQL LIKE.
- Embeddings are currently computed for `file_index.semantic_summary`, not full document chunks.
- This is useful as summary-level RAG, but it is not yet a robust full-document RAG system. Long documents and precise phrase lookup need FTS5/chunk indexing.

### 6. Task And Conversation Layer

The current syscall layer is task-oriented, not a full assistant conversation runtime.

Needed:

- conversation/session table
- message history
- streaming responses
- tool-call trace
- cancellable tasks
- background task progress
- notification-ready event model
- short-term context separate from long-term memory

### 7. Model And Cost Control

Needed:

- local/Ollama first-class path
- cloud provider routing policy
- token budget per task
- sensitive data redaction before cloud calls
- model fallback
- offline fallback
- provider health checks
- latency/cost logging

### 8. Product APIs

Syscall is good internally. Product should expose stable app-level contracts:

- `assistant.chat`
- `profile.get`
- `profile.feedback`
- `memory.search`
- `memory.delete`
- `sources.list`
- `sources.update`
- `index.status`
- `index.rebuild`
- `model.status`
- `privacy.audit`
- `tasks.list`
- `tasks.cancel`

Do not make the frontend know internal agent names.

## Jarvis Backend Metrics

Metrics should cover product quality, reliability, privacy, and cost.

### Product Understanding Metrics

- Profile accuracy: percent of user-reviewed facts marked accurate.
- Profile rejection rate: percent marked inaccurate or hidden.
- Fact confirmation rate: inferred facts that become confirmed.
- Unknown/insufficient-evidence rate: how often assistant correctly refuses to guess.
- Source coverage: percent of approved sources indexed and summarized.
- Freshness: time since last successful scan / summary / profile update.

Good early targets:

- profile accuracy above 80% on reviewed facts
- rejection rate below 15%
- approved source indexing above 95%
- profile freshness below 24 hours for active users

### Retrieval Metrics

- Context hit rate: answer used at least one relevant memory/source.
- Top-k source relevance: user or eval says relevant source appears in top 5.
- Hallucinated-source rate: answer cites or implies nonexistent evidence.
- Query latency to retrieved context.
- Empty retrieval rate for answerable personal questions.

Good early targets:

- top-5 relevance above 80% on a seed eval set
- hallucinated-source rate near 0
- context retrieval under 500ms locally for common queries

### Answer Quality Metrics

- User thumbs-up/down on assistant answers.
- Correction count per answer.
- “That’s not me” rate for personality/profile answers.
- Follow-up success: user does not need to restate context.
- Groundedness: answer claims map to stored facts/sources.

Good early targets:

- answer helpfulness above 70%
- grounded claims above 90%
- repeated-context complaints trending down over time

### Runtime Reliability Metrics

- Daemon startup success rate.
- Time to ready syscall.
- Scan time per 1k files.
- Scheduler queue depth.
- Task timeout rate.
- Crash-free sessions.
- DB migration success rate.
- Memory DB size growth.

Good early targets:

- daemon ready under 3 seconds before scanning
- crash-free sessions above 99%
- task timeout rate below 2%

### Privacy Metrics

- Percent of cloud LLM calls with sensitivity classification.
- Sensitive-send block count.
- User-approved sources vs discovered sources.
- Delete/export success rate.
- Facts with provenance.
- Unreviewed high-confidence personal claims.

Good early targets:

- 100% of profile facts have provenance
- 100% of cloud calls pass through policy gate
- 0 unapproved directories indexed
- delete/export success near 100%

### Cost And Performance Metrics

- Tokens per summarized file.
- Tokens per profile update.
- Cloud cost per active day.
- Local vs cloud model ratio.
- P50/P95 assistant latency.
- P50/P95 indexing latency.
- CPU/memory usage during scan.

Good early targets:

- chat P95 under 8 seconds with cloud model
- local status/profile reads under 300ms
- background scan CPU throttled enough that Mac stays usable

### Suggested MVP Metric Dashboard

First metric screen should show:

```text
Daemon: running / not running
Sources approved: N
Files indexed: N
Summaries complete: %
Profile facts: total / confirmed / rejected / hidden
Last scan: time
Last profile update: time
LLM mode: local / Ollama / API
Recent task failures: N
```

This is more useful for product iteration than raw SSE logs.

## Important Paths

```text
src/cli.py
src/kernel/config.py
src/kernel/daemon.py
src/kernel/cron.py
src/kernel/event_bus.py
src/kernel/user_settings.py
src/filesystem/watcher.py
src/memory/store.py
src/memory/embeddings.py
src/scheduler/pool.py
src/syscall/protocol.py
src/syscall/server.py
src/syscall/client.py
src/agents/base.py
src/agents/builtin.py
src/agents/assistant.py
src/agents/profile_builder.py
src/agents/reporter.py
src/agents/summarizer.py
src/agents/triage.py
src/llm/router.py
src/llm/compatible_adapter.py
src/memory/embeddings.py
src/web/api/clusters.py
src/web/dashboard.py
src/web/_utils.py
config/default.yaml
tests/test_summary_pipeline_optimization.py
tests/test_live_llm_dashscope.py
tests/test_browser_history_ingest.py
desktop/src/main/main.js
desktop/src/preload/preload.js
desktop/src/renderer/src/App.tsx
```

## Quick Commands

```bash
python3 -m compileall src agent_sys
uv run --with pytest --with aiohttp --with openai --with httpx --with numpy python -m pytest
KITH_RUN_LIVE_LLM=1 uv run --with pytest --with aiohttp --with openai --with httpx --with numpy python -m pytest
python3 -m src.cli start
python3 -m src.cli start -d
python3 -m src.cli status
python3 -m src.cli ping
python3 -m src.cli stop
cd desktop && npm run typecheck
cd desktop && npm run build
cd desktop && npm run dev
```

## Notes For Next Session

- The current product direction is a Mac Jarvis-style app for non-developers.
- Backend should remain local-first and privacy-aware.
- Do not expose syscall/token/PID/SSE/model routing language in consumer UI.
- Keep developer dashboard as Advanced/Diagnostics.
- Prefer Unix socket for desktop-to-daemon integration unless `aiohttp` is guaranteed installed.
- If continuing the startup fix, inspect `desktop/src/main/main.js` and `src/filesystem/watcher.py` first.
- If continuing retrieval work, implement Layer 2 first: SQLite FTS5 / grep-like exact text index before expanding chunk embeddings.
- If debugging “file not summarized”, check `triage_status` first. `unknown`, `skip`, and `low` are intentionally excluded from default summarization.
