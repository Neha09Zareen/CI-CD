# src/pipeline.py
"""Failure-handling orchestrator.

Runs the LangGraph agent for a failed workflow run, streaming each step to the
event bus and persisting the result to the failure store. The heavy, blocking
work (GitHub log fetch and the agent's LLM calls) runs in worker threads so the
event loop stays responsive; events are published back onto the loop thread in a
thread-safe way. Any error is captured on the record and emitted, never crashing
the server.
"""

from __future__ import annotations

import asyncio
import json
import logging

from .agent import agent
from .events import bus
from .github_actions import fetch_failed_job_logs
from .models import FailureRecord, StepEvent
from .store import store

logger = logging.getLogger("ci_cd_agent.pipeline")

_LOG_EXCERPT_LIMIT = 4000


async def handle_failure(run_id: int, repo: str) -> None:
    """Process one failed workflow run end to end (background task)."""
    loop = asyncio.get_running_loop()
    record = FailureRecord(run_id=run_id, repo=repo, status="analyzing")
    store.upsert(record)

    def emit(step: str, status: str, detail: str | None = None) -> None:
        event = StepEvent(
            run_id=run_id, repo=repo, step=step, status=status, detail=detail
        )
        record.steps.append(event)
        payload = json.loads(event.model_dump_json())
        # Publish on the loop thread; safe to call from worker threads too.
        loop.call_soon_threadsafe(bus.publish, payload)

    try:
        emit("detected", "completed", repo)

        # 1. Fetch logs (blocking → worker thread)
        emit("fetch_logs", "started")
        raw_logs = await asyncio.to_thread(fetch_failed_job_logs, repo, run_id)
        if not raw_logs:
            emit("fetch_logs", "failed", "no logs retrieved")
            record.status = "error"
            record.error = "Could not retrieve logs for the failed job."
            store.upsert(record)
            emit("done", "failed", record.error)
            return
        record.log_excerpt = raw_logs[:_LOG_EXCERPT_LIMIT]
        emit("fetch_logs", "completed", f"{len(raw_logs)} chars")
        store.upsert(record)

        # 2. Run the agent (chunk → recall → maybe analyze → retain)
        state = {
            "run_id": run_id,
            "repo_name": repo,
            "raw_logs": raw_logs,
            "emit": emit,
        }
        final = await asyncio.to_thread(agent.invoke, state)

        # 3. Read results
        record.suggested_fix = final.get("suggested_fix", "") or ""
        record.source = final.get("source", "none") or "none"
        record.model_tier = final.get("model_tier")
        record.status = "awaiting_review"
        store.upsert(record)
        emit("done", "completed", f"source={record.source}")
        logger.info(
            "Processed run %s in %s (source=%s)", run_id, repo, record.source
        )
    except Exception as exc:  # noqa: BLE001 - never crash the server
        logger.exception("Pipeline failed for run %s in %s", run_id, repo)
        record.status = "error"
        record.error = str(exc)
        store.upsert(record)
        emit("done", "failed", str(exc))
