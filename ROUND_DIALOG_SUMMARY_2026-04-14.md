# This Round Summary

Date: 2026-04-14

This document summarizes the work completed during this conversation round for `agent_sys`.

## Scope

The work in this round focused on:

- scheduling strategy profiles for `triage` / `summarizer` / related agents
- embedding provider support, especially DashScope `qwen3-vl-embedding`
- dashboard controls for scheduling, routing, and embeddings
- LLM text/vision routing behavior
- runtime bug fixes discovered while testing

## Major Changes

### 1. Scheduling Strategy Profiles

Added configurable scheduling strategies so the user can choose how aggressive background work should be:

- `aggressive`
- `balanced`
- `quiet`

Implemented in:

- `src/kernel/config.py`
- `src/kernel/cron.py`
- `config/default.yaml`
- `src/web/dashboard.py`
- `src/web/dashboard.html`

What changed:

- added `cron.adaptive.strategy`
- added strategy-aware fallback scheduling logic
- exposed strategy info to the scheduler snapshot
- added dashboard API + UI for selecting strategy

### 2. Embedding Provider Architecture

Refactored embeddings to support multiple providers:

- `local` via `sentence-transformers`
- `dashscope` via OpenAI-compatible embeddings API
- `openai`

Implemented in:

- `src/memory/embeddings.py`
- `src/kernel/config.py`
- `src/kernel/daemon.py`
- `config/default.yaml`
- `src/web/dashboard.py`
- `src/web/dashboard.html`

What changed:

- introduced `EmbeddingConfig`
- added provider selection and API config
- added DashScope `qwen3-vl-embedding` support
- added dashboard API + UI for editing embedding config
- wired provider initialization during kernel boot

### 3. Dashboard Controls

Extended the dashboard so settings can be changed without editing YAML manually.

Added:

- scheduling strategy selector
- per-function LLM routing editor
- embedding provider editor

Implemented in:

- `src/web/dashboard.py`
- `src/web/dashboard.html`

New APIs added:

- `GET /api/scheduling-strategy`
- `POST /api/scheduling-strategy`
- `GET /api/embedding-info`
- `GET /api/llm-routing`
- `POST /api/llm-routing`
- `GET /api/embedding-config`
- `POST /api/embedding-config`

### 4. Per-Function LLM Routing

The backend support for per-function routing was already being built in this round and was confirmed and extended with dashboard controls.

Current routing model supports:

- global defaults for text vs vision
- per-function override for provider/tier
- explicit vision routing

Functions covered in the UI:

- `triage`
- `summarize`
- `analyze`
- `report`
- `brief`
- `profile`
- `search`

Files involved:

- `src/kernel/config.py`
- `src/llm/router.py`
- `config/default.yaml`
- `src/web/dashboard.py`
- `src/web/dashboard.html`

### 5. Vision Routing Safety Fix

Discovered a bug where image tasks could be routed to Anthropic when the configured vision provider was unavailable.

Observed symptom:

- image summarization failed with unsupported `image_url` content

Root cause:

- vision requests could silently fall back to a non-vision-compatible provider

Fix:

- added a hard failure in `src/llm/router.py` when a vision request resolves to a fallback provider different from the configured vision provider

Result:

- the system now refuses unsafe fallback and emits a clear error instead of sending image payloads to the wrong provider

### 6. Shutdown / Double Stop Fix

Discovered a shutdown crash during daemon stop.

Observed symptom:

- duplicate shutdown path caused `_http_runner.cleanup()` to fail because internals were already torn down

Fixes:

- made `SysAgentKernel.shutdown()` idempotent
- made `SyscallServer.stop()` tolerate repeated cleanup and clear internal references

Files:

- `src/kernel/daemon.py`
- `src/syscall/server.py`

## Runtime Issues Found During Testing

### A. NumPy / Torch / sentence-transformers incompatibility

Observed:

- local embedding provider could crash or fail because `sentence-transformers` imports `torch`
- the environment had a NumPy / compiled dependency mismatch

Action taken:

- made embedding availability checks fail gracefully instead of crashing boot
- moved NumPy imports to lazy runtime use
- wrapped provider checks with error handling

Files:

- `src/memory/embeddings.py`

Important note:

- this only prevented boot-time crashes
- it did not make the incompatible local stack valid

### B. HuggingFace model fetch timeouts

Observed:

- local embedding model loading repeatedly timed out against `huggingface.co`

Action taken:

- forced offline environment flags before local model load to avoid repeated network checks

File:

- `src/memory/embeddings.py`

Important note:

- this reduced wasted time
- it still did not make the local embedding stack the preferred solution for this project

## Final Embedding Direction

The user had requested DashScope `qwen3-vl-embedding` from the beginning.

The final configuration was corrected so the project defaults to DashScope instead of local sentence-transformers.

Updated:

- `config/default.yaml`
- `src/kernel/config.py`
- `src/agents/summarizer.py`

What was corrected:

- default embedding provider changed to `dashscope`
- default model changed to `qwen3-vl-embedding`
- embedding model name persisted from the active provider instead of hardcoded `all-MiniLM-L6-v2`

## Files Touched In This Round

- `config/default.yaml`
- `src/kernel/config.py`
- `src/kernel/cron.py`
- `src/kernel/daemon.py`
- `src/llm/router.py`
- `src/memory/embeddings.py`
- `src/agents/summarizer.py`
- `src/syscall/server.py`
- `src/web/dashboard.py`
- `src/web/dashboard.html`

## Final State

At the end of this round:

- scheduling strategies were implemented and exposed in the dashboard
- per-function LLM routing was configurable in the dashboard
- embedding provider selection was configurable in the dashboard
- DashScope `qwen3-vl-embedding` was set as the default embedding direction
- unsafe vision fallback was blocked
- shutdown double-cleanup crash was fixed
- local embedding boot crashes were made non-fatal

## Caveats

- changing dashboard config still requires daemon restart to apply
- local `sentence-transformers` remains environment-sensitive and is no longer the preferred default path
- vision requests still require a valid configured vision provider API key

