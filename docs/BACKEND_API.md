# Backend API Reference

The AI CI/CD Agent backend (the "Brain"). Base URL is wherever the FastAPI app
runs, e.g. `http://localhost:8000` locally or the public tunnel URL.

Run locally:

```bash
pip install -r requirements.txt
copy .env.example .env   # then fill in GROQ_API_KEY and GITHUB_TOKEN
python -m uvicorn src.main:app --reload --port 8000
```

CORS is enabled (configurable via `CORS_ORIGINS`), so a browser frontend on
another origin can call every `/api/*` endpoint and the SSE stream.

## How it works

1. GitHub sends a `workflow_run` webhook to `POST /webhook`.
2. The server acknowledges immediately and processes the failure in the
   background through the LangGraph agent: chunk logs → recall memory →
   (if new) analyze with the reasoning model → retain the fix.
3. Every step is broadcast over `GET /api/stream` (Server-Sent Events) and the
   result is persisted, readable via the `/api/failures` endpoints.
4. A human approves, edits, or rejects the suggested fix.

If the same error was seen before, the agent serves the stored fix and skips the
LLM entirely (`source: "memory"`).

## Endpoints

### POST `/webhook`
GitHub webhook receiver. Returns quickly. Responses:
- `{"status": "accepted", "run_id": <id>}` — a failure was queued for analysis
- `{"status": "ignored", "reason": "..."}` — non-failure / non-workflow_run event

### GET `/health`
```json
{ "status": "ok",
  "dependencies": { "groq_key_present": true, "github_token_present": true },
  "stream_subscribers": 0 }
```

### GET `/api/stats`
```json
{ "total_failures": 3, "memory_hits": 1, "generated_fixes": 2,
  "approved": 1, "rejected": 0, "errors": 0 }
```

### GET `/api/failures`
Array of failure records, newest first. See the record shape below.

### GET `/api/failures/{run_id}`
One failure record, or `404` if not found.

**Failure record shape:**
```json
{
  "run_id": 28327513895,
  "repo": "owner/name",
  "detected_at": "2026-06-28T21:30:00Z",
  "status": "awaiting_review",
  "log_excerpt": "Traceback... AssertionError ...",
  "suggested_fix": "### Root cause\n...\n### Fix\n...",
  "source": "generated",
  "model_tier": "reasoning",
  "error": null,
  "reject_reason": null,
  "steps": [
    { "type": "step", "run_id": 28327513895, "repo": "owner/name",
      "step": "analyze", "status": "completed",
      "timestamp": "2026-06-28T21:30:05Z", "detail": "model tier: reasoning" }
  ]
}
```
`status` ∈ `analyzing | awaiting_review | approved | rejected | error`.
`source` ∈ `memory | generated | none`.

### POST `/api/failures/{run_id}/approve`
Body (optional): `{ "edited_fix": "..." }`. Marks the fix approved; if
`edited_fix` is provided it replaces the AI suggestion and is what gets retained
in memory. Returns the updated record. `409` if already approved/rejected.

### POST `/api/failures/{run_id}/reject`
Body (optional): `{ "reason": "..." }`. Marks the fix rejected. Returns the
updated record. `409` if already approved/rejected.

### GET `/api/memory`
Object of `signature -> fix` entries the agent has learned.

### DELETE `/api/memory/{key}`
Remove one memory entry. `404` if the key is absent.

### GET `/api/stream` (Server-Sent Events)
`text/event-stream`. Each message is `data: <json>\n\n` where the JSON is a step
event (same shape as items in `steps` above). Lines beginning with `:` are
keepalive/comment frames. Consume from the browser with `EventSource`:

```js
const es = new EventSource(`${BASE_URL}/api/stream`);
es.onmessage = (e) => {
  const event = JSON.parse(e.data);
  // event.run_id, event.step, event.status, event.detail, event.timestamp
};
```

Steps you will see, in order: `detected`, `fetch_logs`, `chunk`, `recall`,
`analyze` (skipped on a memory hit), `retain`, `done`. Approve/reject emit
`approve`/`reject` status events.

## Notes for the frontend

- Subscribe to `/api/stream` on mount to render live progress; also call
  `GET /api/failures` to backfill history (the stream only carries new events).
- A failure becomes actionable when its `status` is `awaiting_review`.
- `source: "memory"` means the fix came instantly from memory with no LLM cost —
  worth surfacing as a badge.
