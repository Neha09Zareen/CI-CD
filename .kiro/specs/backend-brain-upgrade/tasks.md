# Implementation Plan

## Overview

This plan upgrades the backend into a live, human-in-the-loop remediation brain
in dependency order: foundations (config, models) first, then core logic
(memory, parsing, GitHub), then infrastructure (events, store), then the agent
and pipeline, then the API surface, and finally tests and verification. Each task
is incremental, builds on prior tasks, and ends with working, tested code.

## Tasks

- [x] 1. Configuration and data models foundation
- [x] 1.1 Create `src/config.py` with a cached `Settings` loaded from env
  - Load groq_api_key, github_token, fast_model, reasoning_model, store_path, hindsight_path, cors_origins
  - Importing must never raise on missing secrets
  - _Requirements: 8.1, 8.2_
- [x] 1.2 Create `src/models.py` with Pydantic models
  - `FailureRecord`, `StepEvent`, `ApproveBody`, `RejectBody`, status/source literals
  - _Requirements: 5.1, 6.4_
- [x] 1.3 Add unit-test scaffolding (`tests/` package, pytest config)
  - Add `pytest` to requirements.txt
  - _Requirements: 9.4_

- [x] 2. Memory and parsing core upgrades
- [x] 2.1 Upgrade `src/hindsight.py`
  - Add `signature_for(chunk)` used by both recall and retain; normalize whitespace, first 100 chars
  - Add `list_entries()` and `delete_entry(key)`; read path from settings
  - _Requirements: 2.3, 2.4, 5.4_
- [x] 2.2 Write `tests/test_hindsight.py`
  - signature determinism; retain→recall round-trip in a temp file; list/delete
  - _Requirements: 9.1, Property 1_
- [x] 2.3 Upgrade `src/cascade_flow.py`
  - Add `select_culprit_chunk(chunks) -> (index, text)` with last-chunk fallback
  - Make `analyze_logs_with_cascadeflow` return `(fix_text, model_tier)`, reasoning model only on culprit chunk
  - Wrap Groq call in try/except returning a clear message
  - _Requirements: 5.1, 5.2, 5.4, 8.4_
- [x] 2.4 Write `tests/test_cascade.py`
  - chunk overlap/coverage; `overlap>=max_lines` raises; culprit selection picks error chunk and falls back to last
  - _Requirements: 9.1, Property 2, Property 3_

- [x] 3. GitHub client modernization
- [x] 3.1 Upgrade `src/github_actions.py`
  - Initialize with `Github(auth=Auth.Token(token))`
  - Move `fetch_failed_job_logs(repo, run_id)` here; lazy client; clear error if token missing
  - _Requirements: 8.3, 8.2_

- [x] 4. Event streaming infrastructure
- [x] 4.1 Create `src/events.py` with an async `EventBus`
  - `subscribe`/`unsubscribe`/`publish`; non-blocking publish with bounded per-client queue
  - Module-level `bus` singleton
  - _Requirements: 4.4, Property 8_

- [x] 5. Durable failure store
- [x] 5.1 Create `src/store.py` with `FailureStore`
  - `upsert`/`get`/`list`(newest-first)/`stats`; JSON-file backed; lock-guarded; flush on write
  - Load existing file on startup so records survive restart
  - _Requirements: 5.1, 5.6, 7.3_

- [x] 6. Agent rewrite (the brain)
- [x] 6.1 Rewrite `src/agent.py` with relative imports and emit callback
  - `from .hindsight ...`, `from .cascade_parser ...`, `from .cascade_flow ...`
  - State adds `source`, `model_tier`, optional `emit`; each node calls emit
  - _Requirements: 1.1, 1.2, 1.3_
- [x] 6.2 Add conditional recall edge (skip LLM on memory hit)
  - After recall: hit → END with source=memory; miss → cascadeflow → retain → END
  - Retain uses `signature_for` of the culprit/first chunk
  - _Requirements: 2.1, 2.2, 2.5_

- [x] 7. Pipeline orchestrator
- [x] 7.1 Create `src/pipeline.py` `handle_failure(run_id, repo)`
  - Create record(analyzing)→persist→emit detected; fetch logs→emit; run agent with emit callback; read fix/source/model_tier; set awaiting_review→persist→emit done
  - Wrap whole flow in try/except → record error status + emit error event (no crash)
  - _Requirements: 1.1, 3.2, 3.3, 3.4, Property 5_

- [x] 8. API surface rewrite (`src/main.py`)
- [x] 8.1 Rebuild app with CORS, logger, and fast webhook
  - Add `CORSMiddleware` from settings; replace prints with logger
  - `/webhook`: validate, ignore non-failure with 200, enqueue `handle_failure` via BackgroundTasks, return fast
  - _Requirements: 3.1, 5.5, 7.1, Property 4_
- [x] 8.2 Implement failure + memory + stats + health routes
  - `GET /api/failures`, `GET /api/failures/{run_id}` (404 if absent), `GET /api/memory`, `DELETE /api/memory/{key}`, `GET /api/stats`, `GET /health` (boolean key presence only)
  - _Requirements: 5.2, 5.3, 5.4, 7.2, 7.3, 7.4_
- [x] 8.3 Implement approval workflow routes
  - `POST /api/failures/{run_id}/approve` (optional edited_fix → replace + retain to Hindsight, status approved, emit)
  - `POST /api/failures/{run_id}/reject` (optional reason, status rejected, emit)
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, Property 6_
- [x] 8.4 Implement SSE `GET /api/stream`
  - `StreamingResponse` text/event-stream; subscribe→yield `data:` frames; keepalive comments; unsubscribe on disconnect; SSE headers
  - _Requirements: 4.1, 4.2, 4.3, 4.5_

- [x] 9. API tests
- [x] 9.1 Write `tests/test_api.py` with mocked externals
  - monkeypatch Groq analysis + GitHub log fetch; seed store; assert failures list/detail, approve(+edited)→retains+status, reject→status, /health, /api/stats
  - _Requirements: 9.2, 9.3, Property 6, Property 7_

- [x] 10. Integration verification and cleanup
- [x] 10.1 Run full test suite and fix failures
  - `python -m pytest -q` green; remove dead code (old inline logic in main.py, unused ai_agent.py if redundant)
  - _Requirements: 1.4, 9.4_
- [x] 10.2 Boot smoke test
  - Import app, start uvicorn, hit `/health` and `/api/stream`, post a synthetic non-failure webhook → 200; confirm no import-time crash without secrets
  - _Requirements: 8.2_
- [x] 10.3 Update docs and env template
  - Add new env vars (CORS_ORIGINS, model overrides, store paths) to `.env.example`; note new `/api/*` and `/api/stream` for the frontend
  - _Requirements: 8.1_

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1.1"], "description": "Settings foundation" },
    { "wave": 2, "tasks": ["1.2", "2.1", "3.1", "4.1"], "description": "Models, memory, GitHub client, event bus (parallel after settings)" },
    { "wave": 3, "tasks": ["1.3", "2.2", "2.3", "5.1"], "description": "Test scaffold, hindsight tests, cascade routing, failure store" },
    { "wave": 4, "tasks": ["2.4", "6.1"], "description": "Cascade tests; agent rewrite" },
    { "wave": 5, "tasks": ["6.2"], "description": "Conditional recall edge" },
    { "wave": 6, "tasks": ["7.1"], "description": "Pipeline orchestrator" },
    { "wave": 7, "tasks": ["8.1"], "description": "App, CORS, fast webhook" },
    { "wave": 8, "tasks": ["8.2", "8.3", "8.4"], "description": "REST, approval, SSE routes" },
    { "wave": 9, "tasks": ["9.1"], "description": "API tests" },
    { "wave": 10, "tasks": ["10.1", "10.2", "10.3"], "description": "Verify, smoke test, docs" }
  ]
}
```

Visual summary:

```
1.1 ─┬─▶ 1.2 ──▶ 1.3
     │
     ├─▶ 2.1 ──▶ 2.2
     │   2.1 ──▶ 2.3 ──▶ 2.4
     ├─▶ 3.1
     └─▶ 4.1
1.2, 1.1 ───────▶ 5.1
2.1, 2.3, 3.1 ──▶ 6.1 ──▶ 6.2
4.1, 5.1, 6.2 ──▶ 7.1
1.2, 4.1, 5.1, 7.1 ─▶ 8.1 ─▶ 8.2 ─▶ 8.3 ─▶ 8.4
5.1, 8.2, 8.3 ──▶ 9.1
all ────────────▶ 10.1 ─▶ 10.2 ─▶ 10.3
```

## Notes

- Stay within the existing stack; only `pytest` is added as a dev dependency.
- Keep JSON-file persistence for both Hindsight and the failure store to remain
  dependency-light; interfaces hide the backend for a future SQLite swap.
- Deferred (not in this plan): webhook HMAC verification and auto-posting fixes to
  GitHub. Interfaces leave room to add them later.
- All external calls (Groq, GitHub) must be mocked in tests for deterministic,
  offline runs.
- Verify after each major section: run `python -m pytest -q` and a boot smoke
  test before moving on.
