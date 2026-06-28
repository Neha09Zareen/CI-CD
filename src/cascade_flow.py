# src/cascade_flow.py
"""Cascadeflow: smart model routing for log chunks.

A cheap, fast scan (the "triage router") scores each chunk for error signals
and selects the single chunk most likely to contain the root cause. Only that
culprit chunk is sent to the stronger, more expensive reasoning model. This
keeps token spend low while ensuring the chunk that matters gets the best model.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from enum import Enum

from groq import Groq

from .config import get_settings

logger = logging.getLogger("ci_cd_agent.cascadeflow")

# The Groq client is created lazily so importing this module never requires
# GROQ_API_KEY to be present (it's only needed when we actually call the LLM).
_groq_client: Groq | None = None


def get_groq_client() -> Groq:
    """Return a cached Groq client, creating it on first use."""
    global _groq_client
    if _groq_client is None:
        api_key = get_settings().groq_api_key
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set; cannot call the reasoning model"
            )
        _groq_client = Groq(api_key=api_key)
    return _groq_client


# Keywords that suggest a chunk contains the actual failure signal. Weighted so
# stronger indicators (tracebacks, fatal) rank a chunk higher.
ERROR_SIGNALS: dict[str, int] = {
    "traceback": 5,
    "stack trace": 5,
    "fatal": 4,
    "panic": 4,
    "exception": 3,
    "assert": 3,
    "exit code": 3,
    "error": 2,
    "failed": 2,
    "failure": 2,
}


class ModelTier(str, Enum):
    """Available model tiers for routing."""

    FAST = "fast"
    REASONING = "reasoning"


def _models() -> dict[ModelTier, str]:
    settings = get_settings()
    return {
        ModelTier.FAST: settings.fast_model,
        ModelTier.REASONING: settings.reasoning_model,
    }


@dataclass
class RouteDecision:
    """The routing outcome for a single chunk."""

    chunk_index: int
    tier: ModelTier
    model: str
    reason: str


def _score_chunk(chunk: str) -> int:
    """Sum the weights of all error signals present in a chunk."""
    lowered = chunk.lower()
    return sum(weight for sig, weight in ERROR_SIGNALS.items() if sig in lowered)


def select_culprit_chunk(chunks: list[str]) -> tuple[int, str]:
    """Select the chunk most likely to contain the root cause.

    Returns ``(index, chunk_text)``. Picks the highest-scoring chunk; if no
    chunk contains any error signal, falls back to the last chunk (failures
    usually surface near the end of a log). Raises on an empty list.
    """
    if not chunks:
        raise ValueError("Cannot select a culprit chunk from an empty list")

    best_index = 0
    best_score = -1
    for index, chunk in enumerate(chunks):
        score = _score_chunk(chunk)
        if score > best_score:
            best_score = score
            best_index = index

    if best_score <= 0:
        # No error signal anywhere; the tail is the best heuristic.
        best_index = len(chunks) - 1

    return best_index, chunks[best_index]


def classify_chunk(chunk: str) -> ModelTier:
    """Decide which model tier a chunk should be routed to."""
    return ModelTier.REASONING if _score_chunk(chunk) > 0 else ModelTier.FAST


def route_chunks(chunks: list[str]) -> list[RouteDecision]:
    """Produce a routing decision per chunk (for inspection/telemetry)."""
    models = _models()
    decisions: list[RouteDecision] = []
    for index, chunk in enumerate(chunks):
        tier = classify_chunk(chunk)
        reason = (
            "error signal detected"
            if tier is ModelTier.REASONING
            else "no error signal; cheap triage"
        )
        decisions.append(
            RouteDecision(index, tier, models[tier], reason)
        )
    return decisions


_SYSTEM_PROMPT = (
    "You are an expert DevOps AI. Analyze the provided failed CI/CD log chunk. "
    "Identify the root cause of the failure and provide a clear, actionable "
    "fix. Format your output in Markdown, ready to be posted as a GitHub PR "
    "comment."
)


def analyze_logs_with_cascadeflow(chunks: list[str]) -> tuple[str, str]:
    """Route to the culprit chunk and generate a fix with the reasoning model.

    Returns ``(fix_markdown, model_tier_name)``. On any LLM error, returns a
    clear message and the tier that was attempted, never raising.
    """
    if not chunks:
        return ("No logs were available to analyze.", ModelTier.FAST.value)

    index, culprit = select_culprit_chunk(chunks)
    models = _models()
    reasoning_model = models[ModelTier.REASONING]

    logger.info(
        "cascadeflow: culprit chunk #%d selected; routing to %s",
        index,
        reasoning_model,
    )

    try:
        response = get_groq_client().chat.completions.create(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Failed Log Chunk:\n\n{culprit}"},
            ],
            model=reasoning_model,
            temperature=0.2,
            max_tokens=1024,
        )
        return (response.choices[0].message.content, ModelTier.REASONING.value)
    except Exception as exc:  # noqa: BLE001 - surface as message, never crash
        logger.error("cascadeflow: reasoning model call failed: %s", exc)
        return (
            f"Analysis failed while contacting the reasoning model: {exc}",
            ModelTier.REASONING.value,
        )
