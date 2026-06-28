# Requirements Document

## Introduction

The AI CI/CD Agent backend ("the Brain") detects GitHub Actions failures via a
webhook, fetches logs, chunks them, and uses a Groq LLM to generate a suggested
fix. It works end to end, but the designed LangGraph agent and Hindsight memory
are not yet wired into the live flow, nothing is persisted, and there is no API
or live feed for a frontend.

This upgrade delivers a focused, competition-grade core that is achievable in a
short build window and centers on three things judges can see:

1. **It learns** — the agent uses Hindsight memory to resolve repeat failures
   instantly and skip the LLM.
2. **You watch it work** — the frontend receives a live stream of the raw error
   logs and each step the agent takes (chunking, recall, routing, analysis).
3. **You stay in control** — failures are persisted and exposed via an API where
   a human approves, edits, or rejects each fix.

Existing constraints to preserve:
- Stack: FastAPI, Uvicorn, PyGithub, LangGraph, Groq, python-dotenv.
- Secrets live in `.env` (gitignored). No secret may be logged or committed.
- Hindsight memory remains file-based (no Docker).

Out of scope for this build (deferred): webhook HMAC verification and automated
posting of fixes back to GitHub. These are noted as future enhancements.

## Glossary

- **Brain**: the backend agent system that analyzes CI/CD failures.
- **Cascade**: log pre-parsing that splits raw logs into overlapping chunks.
- **cascadeflow**: smart model routing between a cheap triage model and a stronger reasoning model.
- **Hindsight**: persistent memory of past error signatures and their fixes.
- **Culprit chunk**: the log chunk most likely to contain the root cause.
- **Event stream**: a Server-Sent Events (SSE) channel the frontend subscribes to for live updates.
- **Failure record**: the persisted representation of one processed failure.

## Requirements

---

## Requirement 1: Agent-Driven Failure Handling

**User Story:** As a developer, I want the webhook to run failures through the
LangGraph agent, so the designed brain (chunk → recall → analyze → retain) is the
single source of truth instead of duplicated logic in the webhook.

#### Acceptance Criteria
1. WHEN a `workflow_run` event with `conclusion == "failure"` is received, THEN the system SHALL process it through the compiled LangGraph agent.
2. THE agent module SHALL use package-relative imports so it loads under `uvicorn src.main:app` without `ModuleNotFoundError`.
3. WHEN the agent completes, THEN the system SHALL read the suggested fix, the memory-hit flag, and the model tier from the agent's final state.
4. THE duplicated chunk/analyze logic in `main.py` SHALL be removed in favor of the agent path.

## Requirement 2: Memory-Aware Analysis (Hindsight in the loop)

**User Story:** As a maintainer, I want the agent to recall past fixes before
calling the LLM, so repeat failures resolve instantly and token spend drops.

#### Acceptance Criteria
1. WHEN the agent processes a failure, THEN it SHALL query Hindsight for a historical fix before invoking the reasoning LLM.
2. IF a historical fix is found, THEN the system SHALL use it and SHALL skip the LLM analysis step.
3. IF no historical fix is found, THEN the system SHALL generate a new fix and SHALL retain it in Hindsight keyed by a stable error signature.
4. THE recall key and retain key SHALL be derived identically so a retained fix is recallable on the next identical failure.
5. THE result SHALL carry its source as `memory` or `generated`.

## Requirement 3: Asynchronous Processing with Progress Events

**User Story:** As an operator, I want the webhook to acknowledge GitHub quickly
and process in the background while emitting progress, so deliveries never time
out and the UI can follow along.

#### Acceptance Criteria
1. WHEN a valid failure event is received, THEN the system SHALL return a 2xx response promptly (target < 2s) without waiting for analysis.
2. THE heavy work (log fetch, chunk, recall, analyze, retain) SHALL run in a background task.
3. AS each agent step starts and completes, THE system SHALL emit a progress event (step name, status, timestamp) to the event stream and persist the latest status on the failure record.
4. IF background processing raises an error, THEN the error SHALL be recorded on the failure record and emitted as an event, and the server SHALL NOT crash.

## Requirement 4: Real-Time Streaming to the Frontend

**User Story:** As a reviewer watching the dashboard, I want to see the raw error
logs and the agent's steps appear live, so the system's work is visible as it
happens.

#### Acceptance Criteria
1. THE system SHALL expose a streaming endpoint `GET /api/stream` using Server-Sent Events.
2. WHEN a failure is detected, its logs fetched, chunked, recalled, analyzed, and finished, THEN a corresponding event SHALL be pushed to all connected stream clients.
3. EACH event SHALL be JSON with at least: `type`, `run_id`, `repo`, `step`, `status`, `timestamp`, and an optional `detail` (e.g. log excerpt or fix text).
4. THE stream SHALL support multiple simultaneous clients and SHALL not block webhook processing.
5. THE stream endpoint SHALL set headers appropriate for SSE and SHALL be reachable cross-origin.

## Requirement 5: Failure Persistence and Control API

**User Story:** As a human in the loop, I want a stored history of failures and an
API to review them, so the frontend can present them and survive restarts.

#### Acceptance Criteria
1. WHEN a failure is processed, THEN the system SHALL persist a record with: run id, repo, detected timestamp, status, log excerpt, full suggested fix, source (`memory`/`generated`), and model tier.
2. THE system SHALL expose `GET /api/failures` returning records newest-first.
3. THE system SHALL expose `GET /api/failures/{run_id}` returning one full record, or 404 if absent.
4. THE system SHALL expose `GET /api/memory` returning Hindsight entries and `DELETE /api/memory/{key}` to remove one.
5. THE system SHALL enable CORS so a browser frontend on another origin can call all `/api/*` endpoints and the stream.
6. THE persisted store SHALL survive process restarts.

## Requirement 6: Human Approval Workflow

**User Story:** As a reviewer, I want to approve, edit, or reject a suggested fix,
so automation never finalizes without my consent.

#### Acceptance Criteria
1. THE system SHALL expose `POST /api/failures/{run_id}/approve` accepting an optional edited fix body.
2. WHEN a fix is approved with an edited body, THEN the edited body SHALL replace the stored fix and SHALL be what is retained in Hindsight.
3. THE system SHALL expose `POST /api/failures/{run_id}/reject` recording an optional reason.
4. THE failure status SHALL transition through a defined set: `analyzing` → `awaiting_review` → `approved` | `rejected`.
5. WHEN status changes via these endpoints, THEN a corresponding event SHALL be pushed to the stream.

## Requirement 7: Observability and Health

**User Story:** As an operator, I want structured logs, a health check, and basic
stats, so I can monitor and demo the system confidently.

#### Acceptance Criteria
1. THE system SHALL use a configured logger for operational events (bare `print` calls replaced).
2. THE system SHALL expose `GET /health` returning status and dependency readiness (Groq key present, GitHub token present) without revealing secret values.
3. THE system SHALL expose `GET /api/stats` returning counts: total failures, memory hits, generated fixes, approvals, rejections.
4. NO log line or API response SHALL contain secret values.

## Requirement 8: Configuration and Resilience

**User Story:** As a developer, I want centralized config and safe failure
handling, so the service starts reliably and degrades gracefully.

#### Acceptance Criteria
1. THE system SHALL load configuration (tokens, model names, store paths, CORS origins) from environment via a single settings module.
2. THE application SHALL import successfully even when optional credentials are absent; missing credentials SHALL surface as clear runtime errors only when the dependent feature is used.
3. THE PyGithub client SHALL be initialized using the non-deprecated `Auth.Token` API.
4. WHEN a Groq or GitHub call fails, THEN the system SHALL handle it gracefully (record error, emit event) rather than crash.

## Requirement 9: Test Coverage

**User Story:** As a maintainer, I want automated tests for the core logic, so the
upgrade is verifiably correct.

#### Acceptance Criteria
1. THE system SHALL include unit tests for: log chunking, culprit-chunk selection, and Hindsight recall/retain round-trip.
2. THE system SHALL include API tests for the failures list/detail and approve/reject endpoints using mocked external services.
3. WHEN tests run, THEN all external network calls (Groq, GitHub) SHALL be mocked so tests are deterministic and offline.
4. THE suite SHALL run with a single command and SHALL pass.
