---
name: agent-sys-file-search
description: Searches and reads files anywhere on the user's Mac (not just the current workspace) through the locally-running agent-sys daemon. Use when the user asks to find notes, documents, PDFs, projects, or any files located outside the current Cursor workspace, or when they reference files by topic/keyword rather than path. Also exposes the knowledge base of past LLM-generated reports and analyses.
---

# agent-sys: File Search & Knowledge

agent-sys indexes ~30k+ files across the user's machine (typically `~/Documents`, `~/Desktop`, and configured roots) with LLM-generated summaries, tags, and triage priorities. Use this skill to reach files that live outside the current Cursor workspace.

## Precondition

```bash
curl -s http://127.0.0.1:7437/health   # expect {"status": "ok"}
```

If unreachable, tell the user to run `agent-sys start`.

## Auth

Every `/syscall` request must carry `X-Agent-Token: $(cat ~/.agent_sys/auth_token)`.
`/health` and `/status` don't need it. 401 back → token missing / daemon restarted with
a fresh token. Read the token inline in each shell call (don't cache it across turns):

```bash
TOKEN=$(cat ~/.agent_sys/auth_token)
```

## File search

```bash
TOKEN=$(cat ~/.agent_sys/auth_token)
curl -s -X POST http://127.0.0.1:7437/syscall \
  -H "Content-Type: application/json" \
  -H "X-Agent-Token: $TOKEN" \
  -d '{"call_type": "file.search",
       "params": {"query": "database migration", "limit": 20},
       "caller": "cursor"}'
```

Optional params:
- `file_type`: `".md"`, `".pdf"`, `".py"`, etc. — filters by extension.
- `limit`: default 20, raise to 50 for wider nets.

Returned `data.matches` is a list of `{path, score, summary, tags, modified_at, triage}`. Prefer results with `triage` in `{"high", "medium"}`; items marked `"skip"` or `"low"` are usually third-party code and less relevant.

## Read a specific file's indexed record

```bash
TOKEN=$(cat ~/.agent_sys/auth_token)
curl -s -X POST http://127.0.0.1:7437/syscall \
  -H "Content-Type: application/json" \
  -H "X-Agent-Token: $TOKEN" \
  -d '{"call_type": "file.read",
       "params": {"path": "/absolute/path/to/file"},
       "caller": "cursor"}'
```

Returns metadata + LLM summary. For the raw bytes, read the file directly with normal file tools.

## Knowledge base (past reports)

The daemon stores every LLM-generated report. Query by category:

```bash
TOKEN=$(cat ~/.agent_sys/auth_token)
curl -s -X POST http://127.0.0.1:7437/syscall \
  -H "Content-Type: application/json" \
  -H "X-Agent-Token: $TOKEN" \
  -d '{"call_type": "knowledge.query",
       "params": {"category": "daily_report", "limit": 10},
       "caller": "cursor"}'
```

Useful categories:

| Category | Contents |
|---|---|
| `daily_report` | End-of-day summaries |
| `context_brief` | Session briefs generated for agents |
| `behavior_insight` | Output of `analyze.behavior` runs |
| `project_summary` | Per-project roll-ups |
| `scheduling_decision` | LLM-made cron decisions |
| `quick_report` | Lightweight ad-hoc reports |

Omit `category` to browse everything.

## Strategy

1. **Narrow query with keywords from the user's wording.** Don't over-stem.
2. **Trust triage.** If the user asks about "my notes on X", filter to `triage in [high, medium]` when rendering matches.
3. **Prefer indexed summary over re-reading files.** The daemon has already summarized most non-trivial files; cite the summary with the path, and only open the raw file if the user wants detail.
4. **Knowledge query before search** when the question is about conclusions / retrospectives / past reasoning — the knowledge base already distilled it.

## Output format

When returning matches to the user, present as a short list:

```
- `/path/to/file` — one-line summary (modified 2d ago, priority: high)
- `/path/to/other` — one-line summary (modified 5d ago, priority: medium)
```

Don't dump the full JSON. Offer to read any entry in full on request.

## Caveats

- Search is **keyword + vector**, not full-text grep. If the user needs exact-string matches inside file bodies, say so and fall back to system `rg`.
- The index lags real-time edits by ~minutes; if a file was just created/modified, mention that results may be stale.
