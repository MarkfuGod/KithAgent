---
name: agent-sys-admin
description: Operates the agent-sys daemon itself — checking status, starting/stopping, inspecting progress, triggering triage/summarization runs, and managing LLM configuration. Use when the user asks whether agent-sys is running, reports that queries are failing, wants to kick off a manual indexing pass, wants to reconfigure models/API keys, or is troubleshooting the daemon.
---

# agent-sys: Daemon Administration

Operational control over the agent-sys daemon. Prefer the CLI (`agent-sys <cmd>`) for one-shot admin tasks; use HTTP for programmatic checks.

## Auth note

`/health` and `/status` are open. Any deeper HTTP call (`/syscall`, `/reload`) needs
`X-Agent-Token: $(cat ~/.agent_sys/auth_token)`. See `agent-sys-user-context` skill for
the full pattern. Admin tasks below mostly use the CLI, which handles auth itself.

## Status triage flowchart

User says "agent-sys seems broken / slow / missing data". Run in order:

1. **Is the process alive?**
   ```bash
   curl -s http://127.0.0.1:7437/health
   ```
   - `{"status": "ok"}` → daemon is up. Continue.
   - connection refused → not running. Go to step 3.

2. **What's its workload?**
   ```bash
   curl -s http://127.0.0.1:7437/status | python3 -m json.tool
   ```
   Key fields in the response:
   - `filesystem.files_indexed` / `filesystem.scan_progress` — scan completeness
   - `filesystem.scan_in_progress` — true means first-time walk still running
   - `subsystems` — which subsystems booted
   - `pid` — process id

3. **Start / restart:**
   ```bash
   agent-sys start -d        # detached background daemon
   agent-sys start --force   # overwrite a stuck old PID
   agent-sys stop            # clean shutdown
   agent-sys ping            # liveness check
   agent-sys status          # detailed CLI status
   ```

## Manual agent runs

When the user wants to force-refresh instead of waiting for the adaptive cron:

| Command | Purpose |
|---|---|
| `agent-sys triage` | Re-classify file importance (rules + LLM) |
| `agent-sys summarize` | Summarize next batch of high/medium files |
| `agent-sys analyze --hours 24` | Run behavior analysis now |
| `agent-sys report daily` | Force-generate today's daily report |
| `agent-sys profile` | Rebuild personal profile |
| `agent-sys classify` | Re-rank files into P0 / P1 / P2 |

All of these write to the SQLite store at `~/.agent_sys/memory.db`, so the effects are visible to later HTTP queries and to the web dashboard.

## LLM configuration

Config lives at `~/.agent_sys/llm_config.yaml`. Three ways to edit:

1. **Web UI (recommended):** `agent-sys dashboard` → open `http://127.0.0.1:7438` → "LLM Config" tab.
2. **Direct YAML edit:** modify `~/.agent_sys/llm_config.yaml`, then restart the daemon.
3. **Interactive CLI:** first `agent-sys start` after deleting the file re-prompts.

Supported providers: `openai`, `claude`, `anthropic_compatible`, `compatible` (OpenAI-compatible — Ollama / DeepSeek / Qwen).

Task-to-tier routing (`fast` / `strong` / `vision` / `anthropic_compat`) is set in `config/default.yaml` under `llm.routing`. Don't edit this unless the user asks — cron and adaptive scheduling depend on the tier names.

## Troubleshooting patterns

| Symptom | Likely cause | Fix |
|---|---|---|
| `curl` returns connection refused | daemon stopped | `agent-sys start -d` |
| `/syscall` returns 401 `auth failed` | missing/stale `X-Agent-Token` header | read `~/.agent_sys/auth_token` inline per call, don't cache |
| `files_indexed` stuck at low number | first-time scan still walking | wait; check `scan_in_progress` |
| summaries empty for most files | LLM key missing/invalid | check `~/.agent_sys/llm_config.yaml`, test with `agent-sys ping` |
| triage all `null` | triage agent never ran | `agent-sys triage --batch-size 1000` |
| dashboard shows stale data | expected — dashboard reads SQLite, not live state | re-run relevant agent command |

## Web dashboard shortcuts

```bash
agent-sys dashboard                    # http://127.0.0.1:7438
agent-sys dashboard --port 9000        # alternate port
```

Dashboard reads SQLite directly, so it works even when the daemon is stopped — useful for forensics.

## Safety

- Never run `agent-sys stop --force` on a production user unless asked.
- Never delete `~/.agent_sys/memory.db` without confirming — it holds every LLM-generated summary and report, which is expensive to regenerate.
- If editing `config/default.yaml` in the repo, remind the user the daemon must be restarted.
