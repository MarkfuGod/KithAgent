---
name: agent-sys-file-search
description: Searches, reads, and answers over files anywhere on the user's Mac (not just the current workspace) through the locally-running agent-sys daemon. Use when the user asks to find notes, documents, PDFs, projects, images, or files located outside the current Cursor workspace; when they reference files by topic/keyword rather than path; or when they need a citation-backed answer using hybrid RAG evidence. Also exposes the knowledge base of past LLM-generated reports and analyses.
---

# agent-sys: File Search & Knowledge

agent-sys indexes files across the user's machine (typically `~/Documents`, `~/Desktop`, and configured roots) with LLM-generated summaries, tags, triage priorities, and delayed RAG chunks. Use this skill to reach files that live outside the current Cursor workspace.

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

## Citation-backed RAG answer

When the user asks a question that needs synthesis over local files, prefer `assistant.chat` over a raw file list. It uses hybrid retrieval over text chunks, images, and scanned-PDF evidence, then returns an answer plus source metadata.

```bash
TOKEN=$(cat ~/.agent_sys/auth_token)
curl -s -X POST http://127.0.0.1:7437/syscall \
  -H "Content-Type: application/json" \
  -H "X-Agent-Token: $TOKEN" \
  -d '{"call_type": "assistant.chat",
       "params": {"message": "What do my notes say about database migrations?"},
       "caller": "cursor"}'
```

Use `data.answer` as the draft response and cite `data.sources[*].source_id` (for example `[S1]`) when grounding important claims. Source metadata may include:

- `modality`: `text` or `image`
- `source_kind`: `text`, `image`, or `scanned_pdf`
- `page`: scanned-PDF page when available
- `start_line` / `end_line`: text chunk line range when available

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

1. **Use `assistant.chat` for answers, `file.search` for lists.** If the user asks "what do my files say about X", get a citation-backed answer. If they ask "find files about X", return paths.
2. **Narrow query with keywords from the user's wording.** Don't over-stem.
3. **Trust triage.** If the user asks about "my notes on X", filter to `triage in [high, medium]` when rendering matches.
4. **Prefer indexed summary/RAG evidence over re-reading files.** The daemon has already summarized and chunked most useful files; open the raw file only if the user wants detail.
5. **Knowledge query before search** when the question is about conclusions / retrospectives / past reasoning — the knowledge base already distilled it.

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
- RAG vector recall requires a configured embedding provider. Without one, chunk + FTS retrieval still works, but image/scanned-PDF recall will be weaker.
