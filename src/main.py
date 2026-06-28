"""FastAPI application entrypoint for the AI CI/CD Agent.

Thin, fast HTTP surface for the Brain:
- POST /webhook            receive GitHub workflow_run events, ack fast, process in background
- GET  /health             service + dependency readiness
- GET  /api/failures       list processed failures (newest first)
- GET  /api/failures/{id}  one failure record
- POST /api/failures/{id}/approve   approve (optionally edited) fix
- POST /api/failures/{id}/reject    reject with optional reason
- GET  /api/memory         list Hindsight entries
- DELETE /api/memory/{key} remove a Hindsight entry
- GET  /api/stats          aggregate counts
- GET  /api/stream         Server-Sent Events feed of live progress

Run locally with:  uvicorn src.main:app --reload
"""

from __future__ import annotations

import asyncio
import json
import logging

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config import get_settings
from .events import bus
from .hindsight import delete_entry, list_entries, retain_successful_fix
from .models import ApproveBody, RejectBody, StepEvent
from .pipeline import handle_failure
from .store import store

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ci_cd_agent")

settings = get_settings()

app = FastAPI(title="AI CI/CD Agent", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------
@app.post("/webhook")
async def github_webhook(request: Request, background: BackgroundTasks):
    """Receive a GitHub webhook, acknowledge fast, process failures async."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    workflow_run = payload.get("workflow_run")
    if not workflow_run:
        return {"status": "ignored", "reason": "No workflow_run in payload"}

    conclusion = workflow_run.get("conclusion")
    if conclusion != "failure":
        return {"status": "ignored", "reason": f"Conclusion was {conclusion}"}

    run_id = workflow_run.get("id")
    repo_name = payload.get("repository", {}).get("full_name")
    if run_id is None or not repo_name:
        return {"status": "ignored", "reason": "Missing run id or repository"}

    logger.info("Failure accepted: run %s in %s", run_id, repo_name)
    # No heavy work on the request path — hand off to the background.
    background.add_task(handle_failure, int(run_id), repo_name)
    return {"status": "accepted", "run_id": run_id}


# ---------------------------------------------------------------------------
# Health & stats
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    """Report service status and dependency readiness (no secret values)."""
    return {
        "status": "ok",
        "dependencies": {
            "groq_key_present": settings.has_groq,
            "github_token_present": settings.has_github,
        },
        "stream_subscribers": bus.subscriber_count,
    }


@app.get("/api/stats")
def stats() -> dict:
    return store.stats()


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------
@app.get("/api/failures")
def list_failures() -> list[dict]:
    return [json.loads(r.model_dump_json()) for r in store.list()]


@app.get("/api/failures/{run_id}")
def get_failure(run_id: int) -> dict:
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Failure not found")
    return json.loads(record.model_dump_json())


def _emit_status(record, step: str, detail: str | None = None) -> None:
    event = StepEvent(
        run_id=record.run_id,
        repo=record.repo,
        type="status",
        step=step,
        status=record.status,
        detail=detail,
    )
    record.steps.append(event)
    bus.publish(json.loads(event.model_dump_json()))


@app.post("/api/failures/{run_id}/approve")
def approve_failure(run_id: int, body: ApproveBody | None = None) -> dict:
    body = body or ApproveBody()
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Failure not found")
    if record.status in ("approved", "rejected"):
        raise HTTPException(
            status_code=409, detail=f"Already {record.status}"
        )

    if body.edited_fix is not None:
        record.suggested_fix = body.edited_fix

    # Retain the (possibly edited) fix so the next identical failure is instant.
    culprit = record.log_excerpt
    if record.suggested_fix and culprit:
        retain_successful_fix(culprit, record.suggested_fix)

    record.status = "approved"
    store.upsert(record)
    _emit_status(record, "approve", "fix approved")
    return json.loads(record.model_dump_json())


@app.post("/api/failures/{run_id}/reject")
def reject_failure(run_id: int, body: RejectBody | None = None) -> dict:
    body = body or RejectBody()
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Failure not found")
    if record.status in ("approved", "rejected"):
        raise HTTPException(
            status_code=409, detail=f"Already {record.status}"
        )

    record.status = "rejected"
    record.reject_reason = body.reason
    store.upsert(record)
    _emit_status(record, "reject", body.reason)
    return json.loads(record.model_dump_json())


# ---------------------------------------------------------------------------
# Memory (Hindsight)
# ---------------------------------------------------------------------------
@app.get("/api/memory")
def get_memory() -> dict:
    return list_entries()


@app.delete("/api/memory/{key}")
def remove_memory(key: str) -> dict:
    removed = delete_entry(key)
    if not removed:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    return {"status": "deleted", "key": key}


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------
@app.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    """Server-Sent Events feed of live pipeline progress."""

    async def event_generator():
        queue = bus.subscribe()
        try:
            # Initial comment so clients know the stream is open.
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive to hold the connection through idle periods.
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
