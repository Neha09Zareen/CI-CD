# src/models.py
"""Pydantic data models for the AI CI/CD Agent backend.

These define the persisted failure record, the streamed step events, and the
request bodies for the human approval workflow.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

FailureStatus = Literal[
    "analyzing", "awaiting_review", "approved", "rejected", "error"
]
FixSource = Literal["memory", "generated", "none"]

# Allowed forward transitions for a failure record's status. Used to enforce
# status monotonicity (no regressing to an earlier state).
STATUS_ORDER: dict[str, int] = {
    "analyzing": 0,
    "awaiting_review": 1,
    "approved": 2,
    "rejected": 2,
    "error": 2,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StepEvent(BaseModel):
    """A single progress event emitted as the agent works."""

    type: str = "step"  # step | status | error
    run_id: int
    repo: str
    step: str  # detected|fetch_logs|chunk|recall|analyze|retain|done|approve|reject
    status: str  # started|completed|skipped|failed
    timestamp: datetime = Field(default_factory=_now)
    detail: Optional[str] = None


class FailureRecord(BaseModel):
    """The persisted representation of one processed pipeline failure."""

    run_id: int
    repo: str
    detected_at: datetime = Field(default_factory=_now)
    status: FailureStatus = "analyzing"
    log_excerpt: str = ""
    suggested_fix: str = ""
    source: FixSource = "none"
    model_tier: Optional[str] = None
    error: Optional[str] = None
    reject_reason: Optional[str] = None
    steps: list[StepEvent] = Field(default_factory=list)


class ApproveBody(BaseModel):
    """Body for the approve endpoint; an optional human-edited fix."""

    edited_fix: Optional[str] = None


class RejectBody(BaseModel):
    """Body for the reject endpoint; an optional reason."""

    reason: Optional[str] = None
