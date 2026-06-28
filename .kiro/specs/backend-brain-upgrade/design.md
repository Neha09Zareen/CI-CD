# Design Document

## Overview

This design upgrades the AI CI/CD Agent backend into a live, human-in-the-loop
remediation brain. It wires the existing LangGraph agent and Hindsight memory
into the request flow, processes failures asynchronously while broadcasting
progress over Server-Sent Events (SSE), persists every failure to a durable
store, and exposes a REST + SSE API the frontend consumes.

The guiding architectural idea: the webhook is a thin, fast entrypoint. It
validates and enqueues work, then returns. A background task runs the agent,
which emits events at each step and writes results to the store. The frontend
reads the store via REST and subscribes to the SSE stream for live updates.

```
GitHub ──webhook──▶ FastAPI (fast ack) ──▶ background task ──▶ LangGraph agent
                                   │                              │
                                   │                       emits step events
                                   ▼                              ▼
                            Failure Store ◀───── writes ──── EventBus (SSE)
                                   ▲                              │
        Frontend ──REST /api/*─────┘            Frontend ──SSE /api/stream──┘
```

## Architecture

### Module layout (new and changed)

```
src/
├── main.py            # FastAPI app: webhook + /api routes + SSE + CORS (rewritten)
├── config.py          # NEW: centralized Settings loaded from env
├── events.py          # NEW: in-process async EventBus for SSE fan-out
├── store.py           # NEW: durable FailureStore (JSON-file backed)
├── models.py          # NEW: Pydantic models (FailureRecord, events, API bodies)
├── pipeline.py        # NEW: orchestrates agent run + events + persistence
├── agent.py           # CHANGED: relative imports; conditional recall→skip-LLM edge; emits steps
├── cascade_flow.py    # CHANGED: culprit-chunk selection + tier reporting + safe errors
├── cascade_parser.py  # unchanged (already solid)
├── hindsight.py       # CHANGED: stable signature helper; list/delete helpers
├── github_actions.py  # CHANGED: Auth.Token; log fetch moved here
└── memory.py          # unchanged
tests/
├── test_cascade.py        # NEW
├── test_hindsight.py      # NEW
└── test_api.py            # NEW
```

### Why a background task + EventBus (not direct processing)

GitHub expects a webhook response within ~10s and marks slow deliveries failed.
LLM analysis can take longer. So the webhook returns immediately and hands the
job to FastAPI's `BackgroundTasks`. The agent emits events to an in-process
async `EventBus`, which fans them out to every connected SSE client. This keeps
the webhook fast (Req 3) and makes the agent's work visible live (Req 4) without
extra infrastructure (no Redis/Celery needed for a single-process demo).

## Components and Interfaces

### config.py — Settings
A single source of configuration read from environment.

```python
class Settings:
    groq_api_key: str | None
    github_token: str | None
    fast_model: str = "llama-3.1-8b-instant"
    reasoning_model: str = "llama-3.3-70b-versatile"
    store_path: str = "failure_store.json"
    hindsight_path: str = "hindsight_db.json"
    cors_origins: list[str]  # from CORS_ORIGINS csv, default ["*"]

def get_settings() -> Settings: ...  # cached
```

Importing this never raises on missing secrets (Req 8.2). Features that need a
secret check it at call time and raise a clear error.

### models.py — Data models

```python
FailureStatus = Literal["analyzing", "awaiting_review", "approved", "rejected", "error"]
FixSource     = Literal["memory", "generated", "none"]

class FailureRecord(BaseModel):
    run_id: int
    repo: str
    detected_at: datetime
    status: FailureStatus
    log_excerpt: str = ""
    suggested_fix: str = ""
    source: FixSource = "none"
    model_tier: str | None = None
    error: str | None = None
    reject_reason: str | None = None
    steps: list[StepEvent] = []

class StepEvent(BaseModel):
    type: str = "step"           # step | status | error
    run_id: int
    repo: str
    step: str                    # detected|fetch_logs|chunk|recall|analyze|retain|done
    status: str                  # started|completed|skipped|failed
    timestamp: datetime
    detail: str | None = None

class ApproveBody(BaseModel):
    edited_fix: str | None = None

class RejectBody(BaseModel):
    reason: str | None = None
```

### events.py — EventBus (SSE fan-out)
An async pub/sub using one `asyncio.Queue` per subscriber.

```python
class EventBus:
    def subscribe(self) -> asyncio.Queue[str]: ...     # returns a new queue
    def unsubscribe(self, q) -> None: ...
    async def publish(self, event: dict) -> None: ...   # json-encode, push to all queues

bus = EventBus()  # module-level singleton
```

`publish` is non-blocking (uses `put_nowait`, drops to a bounded buffer if a slow
client backs up) so it never stalls the pipeline (Req 4.4).

### store.py — FailureStore
Durable, restart-surviving store backed by a JSON file (Req 5.6). Single-process
demo, so a module-level dict + file flush is sufficient and simple.

```python
class FailureStore:
    def upsert(self, record: FailureRecord) -> None: ...
    def get(self, run_id: int) -> FailureRecord | None: ...
    def list(self) -> list[FailureRecord]: ...        # newest first
    def stats(self) -> dict: ...
```

Writes are serialized with a lock and flushed to disk on each upsert. JSON is
chosen over SQLite to stay dependency-free and match the existing Hindsight
approach; the interface hides the backend so it can be swapped later.

### pipeline.py — Orchestrator
Glue between webhook, agent, store, and event bus. This is where Req 1–3 meet.

```python
async def handle_failure(run_id, repo) -> None:
    # 1. create record(status=analyzing); persist; emit "detected"
    # 2. fetch logs (github_actions) -> emit fetch_logs
    # 3. run the LangGraph agent with an emit callback for each node
    # 4. read final state: fix, source, model_tier
    # 5. record.status = awaiting_review; persist; emit "done"
    # on exception: record.status=error, record.error=...; emit "error"
```

The agent nodes call an injected `emit(step, status, detail)` callback so
chunk/recall/analyze/retain each surface to the stream.

### agent.py — LangGraph brain (changed)
- Relative imports: `from .hindsight import ...`, `from .cascade_parser import ...`, `from .cascade_flow import ...` (Req 1.2).
- State gains `source`, `model_tier`, and an optional `emit` callable.
- **Conditional edge** after recall: if a historical fix is found, route directly
  to `retain`/`END` and skip `cascadeflow` (Req 2.2). Otherwise go to analysis.
- Each node invokes `state["emit"]` if present.

```
START → chunk_logs → hindsight_recall → (conditional)
                                         ├─ hit  → END           (source=memory)
                                         └─ miss → cascadeflow → hindsight_retain → END
```

### cascade_flow.py — routing (changed)
- Add `select_culprit_chunk(chunks) -> tuple[int, str]`: pick the chunk with the
  highest error-signal score; if none scores, return the last chunk (Req 5.4).
- `analyze_logs_with_cascadeflow` returns `(fix_text, model_tier)` and only the
  culprit chunk is sent to the reasoning model (Req 5.2).
- Wrap the Groq call in try/except; on failure return a clear message and let the
  pipeline record the error (Req 8.4).
- Keep the lazy `get_groq_client()`.

### hindsight.py — memory (changed)
- Add `signature_for(chunk: str) -> str`: the single stable key derivation used by
  both recall and retain (Req 2.4). Normalizes whitespace, takes first 100 chars.
- Add `list_entries() -> dict` and `delete_entry(key) -> bool` for the API.
- Path comes from settings.

### github_actions.py — GitHub (changed)
- Initialize with `Github(auth=Auth.Token(token))` (Req 8.3).
- Move `fetch_failed_job_logs(repo, run_id) -> str` here from `main.py`.

### main.py — API surface (rewritten)
FastAPI app with CORS middleware and these routes:

| Method | Path | Requirement |
| ------ | ---- | ----------- |
| POST | `/webhook` | 1, 3 — validate, ack fast, enqueue `handle_failure` |
| GET | `/health` | 7.2 |
| GET | `/api/stats` | 7.3 |
| GET | `/api/failures` | 5.2 |
| GET | `/api/failures/{run_id}` | 5.3 |
| POST | `/api/failures/{run_id}/approve` | 6.1 |
| POST | `/api/failures/{run_id}/reject` | 6.3 |
| GET | `/api/memory` | 5.4 |
| DELETE | `/api/memory/{key}` | 5.4 |
| GET | `/api/stream` | 4 — SSE via `StreamingResponse` |

## Data Models

See `models.py` above. The SSE event payload mirrors `StepEvent`. The frontend
event contract (Req 4.3):

```json
{ "type": "step", "run_id": 123, "repo": "owner/name",
  "step": "analyze", "status": "completed",
  "timestamp": "2026-06-28T21:40:00Z",
  "detail": "Routed culprit chunk to llama-3.3-70b-versatile" }
```

## SSE Endpoint Design

`GET /api/stream` returns a `StreamingResponse` with media type
`text/event-stream`. The handler subscribes to the bus, then yields
`data: <json>\n\n` frames as events arrive, with a periodic `: keepalive\n\n`
comment to hold the connection open. On client disconnect it unsubscribes.
Headers: `Cache-Control: no-cache`, `Connection: keep-alive`,
`X-Accel-Buffering: no`. CORS is handled by middleware.

## Error Handling

- **Webhook**: invalid JSON → 400; non-`workflow_run` → 200 ignored; non-failure
  conclusion → 200 ignored. Never raise to the client for normal events.
- **Background pipeline**: any exception is caught, the record is marked `error`
  with the message, an `error` event is published, and the server stays up
  (Req 3.4, 8.4).
- **Groq/GitHub failures**: caught in their modules, surfaced as recorded errors,
  not crashes.
- **Missing secrets**: import succeeds; the dependent call raises a clear,
  non-secret error message (Req 8.2).
- **Secret hygiene**: logger formats only non-secret fields; `/health` reports
  booleans for key presence, never values (Req 7.4).

## Testing Strategy

Unit (offline, no network):
- `test_cascade.py`: chunking boundaries/overlap, `overlap>=max_lines` raises,
  `select_culprit_chunk` picks the error chunk and falls back to last chunk.
- `test_hindsight.py`: `signature_for` stability; retain→recall round-trip in a
  temp file; `list_entries`/`delete_entry`.

API (mocked):
- `test_api.py` with FastAPI `TestClient`: monkeypatch the Groq call and GitHub
  log fetch; post a synthetic failure record into the store; assert
  `/api/failures`, `/api/failures/{id}`, approve (with edited body), reject,
  `/health`, `/api/stats`. Verify approve retains to Hindsight and updates status.

All external calls are monkeypatched so the suite is deterministic and offline
(Req 9.3). Run with `python -m pytest -q`.

## Key Design Decisions and Trade-offs

1. **SSE over WebSockets** — one-way server→client updates are all the UI needs;
   SSE is simpler, works over plain HTTP, and auto-reconnects in browsers.
2. **In-process EventBus + BackgroundTasks** — no Redis/Celery; perfect for a
   single-process hackathon deployment behind one tunnel. The bus interface
   allows a later swap to Redis pub/sub if scaled.
3. **JSON-file stores** — keeps the project dependency-light and consistent with
   Hindsight; `FailureStore`/`Hindsight` interfaces hide the backend so SQLite is
   a drop-in later.
4. **Conditional skip-LLM edge** — makes the "it learns and saves cost" claim
   concrete and demoable: a repeated failure resolves with `source=memory` and no
   Groq call.
5. **Deferred (documented) scope** — webhook HMAC verification and auto-posting to
   GitHub are intentionally out of this build to fit the window; interfaces leave
   room to add them.

## Requirements Coverage

- R1 → pipeline runs agent; agent.py relative imports; main.py slimmed.
- R2 → conditional recall edge; `signature_for`; source flag.
- R3 → BackgroundTasks + per-step events + error capture.
- R4 → EventBus + `/api/stream` SSE.
- R5 → FailureStore + `/api/failures*`, `/api/memory*`, CORS, disk persistence.
- R6 → approve/reject endpoints, status transitions, edited-fix retain, events.
- R7 → logger, `/health`, `/api/stats`, secret hygiene.
- R8 → config.py, lazy clients, `Auth.Token`, graceful handling.
- R9 → tests/ with mocked externals, single-command run.

## Correctness Properties

These invariants must hold regardless of input and are the basis for tests.

### Property 1: Signature symmetry
`signature_for(x)` is deterministic, and a fix retained under `signature_for(x)`
is always recalled for the same `x` (round-trip property).

**Validates: Requirements 2.4**

### Property 2: Chunk coverage
Every line of the input log appears in at least one chunk, and consecutive chunks
overlap by exactly `overlap` lines until the final chunk.

**Validates: Requirements 5.1**

### Property 3: Culprit selection totality
`select_culprit_chunk` returns a valid in-range index for any non-empty chunk
list (never raises), and falls back to the last chunk when no error signal is
present.

**Validates: Requirements 5.4**

### Property 4: Fast-ack invariant
The webhook handler performs no LLM or log-fetch work on the request path; those
occur only in the background task.

**Validates: Requirements 3.1, 3.2**

### Property 5: No-crash invariant
Any exception in background processing results in a persisted `error` status and
an emitted error event, never an unhandled server crash.

**Validates: Requirements 3.4, 8.4**

### Property 6: Status monotonicity
A failure record's status only advances along
`analyzing → awaiting_review → {approved|rejected}` (or `→ error`); it never
regresses to an earlier state.

**Validates: Requirements 6.4**

### Property 7: Secret non-disclosure
No API response or log line contains the literal value of any token or API key.

**Validates: Requirements 7.4**

### Property 8: Stream non-blocking
Publishing an event never blocks or fails pipeline progress even if a subscriber
is slow or disconnected.

**Validates: Requirements 4.4**
