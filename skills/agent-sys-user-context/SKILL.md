---
name: agent-sys-user-context
description: Fetches personalized context about the user and citation-backed assistant answers from the locally-running agent-sys daemon via HTTP. Use whenever the user asks the assistant to "get to know me", references their own habits / projects / preferences, asks "what have I been working on", requests personalized recommendations, or asks an open-ended question that should use their local profile, reports, files, or RAG evidence.
---

# agent-sys: User Context

agent-sys is a background daemon on the user's Mac that continuously indexes their files, classifies importance, builds a personal profile with LLMs, and maintains a hybrid RAG index for citation-backed answers. It exposes an HTTP API on `127.0.0.1:7437`. Use this skill to pull user context into the conversation when it would improve personalization.

## Precondition: is the daemon up?

Before any call, confirm the daemon is running:

```bash
curl -s http://127.0.0.1:7437/health
# {"status": "ok"}  → proceed
# connection refused → tell the user to run `agent-sys start` and stop
```

If it's down, do NOT try to start it silently — just tell the user `agent-sys start` (or `agent-sys start -d` for background).

## Auth: every `/syscall` call needs a token

`/health` and `/status` are open. `/syscall` (everything interesting) requires header
`X-Agent-Token: <token>`. The token lives at `~/.agent_sys/auth_token` and is generated
by the daemon on first boot.

Always read it inline in the same shell invocation:

```bash
TOKEN=$(cat ~/.agent_sys/auth_token)
curl -s -X POST http://127.0.0.1:7437/syscall \
  -H "Content-Type: application/json" \
  -H "X-Agent-Token: $TOKEN" \
  -d '{"call_type": "report.brief", "params": {}, "caller": "cursor"}'
```

If the call returns `{"error": "auth failed: ..."}` with HTTP 401, the token is
stale or missing — tell the user to check `~/.agent_sys/auth_token` exists and
the daemon is healthy.

## The context calls

All calls use the same POST shape (see above). Pick based on what the user needs:

| Call | When to use | Cost |
|---|---|---|
| `assistant.chat` | Best default for open-ended personalized questions; uses profile, recent files, reports, and RAG evidence with `sources` | Medium, triggers LLM |
| `report.brief` | First call of a new session when you need a quick "who is this user" snapshot | Cheap, cached |
| `profile.get` | User explicitly asks about their own profile / "what do you know about me" | Cheap, cached |
| `analyze.behavior` | User asks "what have I been working on" / "last N hours/days" | Medium, triggers LLM |
| `report.daily` | User wants today's full report across work / learning / life | Medium, triggers LLM |

### Invocation pattern

For `assistant.chat`, pass the user's question and optional short chat history:

```bash
TOKEN=$(cat ~/.agent_sys/auth_token)
curl -s -X POST http://127.0.0.1:7437/syscall \
  -H "Content-Type: application/json" \
  -H "X-Agent-Token: $TOKEN" \
  -d '{"call_type": "assistant.chat",
       "params": {"message": "What should I focus on this week?", "history": []},
       "caller": "cursor"}'
```

Use `data.answer` for the user-facing reply. `data.sources` contains RAG evidence with fields such as `source_id`, `path`, `snippet`, `modality`, `source_kind`, `page`, and line ranges. Cite important claims with source IDs like `[S1]`, but don't dump the full source JSON.

For `analyze.behavior`, pass `hours`:

```bash
TOKEN=$(cat ~/.agent_sys/auth_token)
curl -s -X POST http://127.0.0.1:7437/syscall \
  -H "Content-Type: application/json" \
  -H "X-Agent-Token: $TOKEN" \
  -d '{"call_type": "analyze.behavior", "params": {"hours": 24}, "caller": "cursor"}'
```

Response shape:

```json
{
  "request_id": "...",
  "success": true,
  "data": { /* call-specific payload */ },
  "elapsed_ms": 42
}
```

Parse `data` and summarize — don't dump raw JSON at the user.

## Decision rules

- **Ambiguous personalization request** (e.g. "recommend a workflow for me") → use `assistant.chat`; it will combine profile, reports, recent files, and RAG evidence.
- **Time-scoped question** ("what did I do this week") → `analyze.behavior` with `hours=168`.
- **Explicit meta question** ("who do you think I am" / "read my profile") → `profile.get`.
- **Working-directory question** ("summarize this project") → this skill is NOT the right tool; use `agent-sys-file-search` or local tools instead.

## Cross-session memory

To persist notes that future Cursor sessions should see:

```bash
TOKEN=$(cat ~/.agent_sys/auth_token)
curl -s -X POST http://127.0.0.1:7437/syscall \
  -H "Content-Type: application/json" \
  -H "X-Agent-Token: $TOKEN" \
  -d '{"call_type": "context.save",
       "params": {"session_id": "cursor-<slug>", "context_data": {"topic": "..."}, "ttl": 86400},
       "caller": "cursor"}'
```

Load with `context.load` and the same `session_id`.

## Output etiquette

- Treat retrieved data as private user context: never paste full profile JSON into the chat unless the user asks.
- Mention that info came from "your agent-sys daemon" the first time per session, then drop the preamble.
- If a call returns `success: false`, show the `error` field and move on — don't retry more than once.
