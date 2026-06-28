# src/cascade_flow.py
"""Cascadeflow: smart model routing for log chunks.

Implements the mandatory cascadeflow requirement. Each log chunk is first
triaged cheaply to decide whether it warrants deeper analysis. Chunks that
look like noise are handled by a fast, low-cost model; chunks that appear to
contain the actual failure are escalated to a stronger reasoning model.

This keeps token spend low (most CI log lines are noise) while ensuring the
chunk holding the root cause gets the best model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from groq import Groq

# The Groq client is created lazily so importing this module never requires
# GROQ_API_KEY to be present (it's only needed when we actually call the LLM).
_groq_client: Groq | None = None


def get_groq_client() -> Groq:
    """Return a cached Groq client, creating it on first use.

    Ensure GROQ_API_KEY is in .env before the first analysis call.
    """
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _groq_client

# Keywords that suggest a chunk contains the actual failure signal and
# should be escalated to the stronger model.
ERROR_SIGNALS = (
    "error",
    "exception",
    "traceback",
    "failed",
    "failure",
    "fatal",
    "assert",
    "panic",
    "exit code",
    "stack trace",
)


class ModelTier(str, Enum):
    """Available model tiers for routing."""

    FAST = "fast"      # cheap, high-throughput triage model
    REASONING = "reasoning"  # stronger, slower, higher-cost model


# Concrete model names per tier. Swap these for whatever Groq models you use.
TIER_MODELS: dict[ModelTier, str] = {
    ModelTier.FAST: "llama-3.1-8b-instant",
    ModelTier.REASONING: "llama-3.3-70b-versatile",
}


@dataclass
class RouteDecision:
    """The routing outcome for a single chunk."""

    chunk_index: int
    tier: ModelTier
    model: str
    reason: str


def classify_chunk(chunk: str) -> ModelTier:
    """Decide which model tier a chunk should be routed to.

    A chunk is escalated to the reasoning tier if it contains any known
    error signal; otherwise it stays on the fast tier.
    """
    lowered = chunk.lower()
    if any(signal in lowered for signal in ERROR_SIGNALS):
        return ModelTier.REASONING
    return ModelTier.FAST


def route_chunks(chunks: list[str]) -> list[RouteDecision]:
    """Route each chunk to a model tier via cascadeflow.

    Args:
        chunks: Log chunks produced by ``cascade_chunk_logs``.

    Returns:
        A routing decision per chunk, in input order.
    """
    decisions: list[RouteDecision] = []
    for index, chunk in enumerate(chunks):
        tier = classify_chunk(chunk)
        reason = (
            "error signal detected"
            if tier is ModelTier.REASONING
            else "no error signal; cheap triage"
        )
        decisions.append(
            RouteDecision(
                chunk_index=index,
                tier=tier,
                model=TIER_MODELS[tier],
                reason=reason,
            )
        )
    return decisions


def analyze_logs_with_cascadeflow(chunks: list[str]) -> str:
    """Mandatory cascadeflow logic: Smart model routing.

    Routes log chunks to find the error efficiently, then uses a heavy
    reasoning model to generate a fix.
    """
    cheap_model = "llama-3.1-8b-instant"
    reasoning_model = "llama-3.3-70b-versatile"

    error_chunk = None

    # 1. Cost-Efficient Routing: Scan for the failure point
    # For a hackathon, a regex/keyword scan is an incredibly fast "cheap
    # router", but you could also prompt the 8B model here to classify chunks.
    for chunk in chunks:
        lower_chunk = chunk.lower()
        if (
            "error" in lower_chunk
            or "exception" in lower_chunk
            or "traceback" in lower_chunk
            or "failed" in lower_chunk
        ):
            error_chunk = chunk
            break  # We found the culprit chunk

    if not error_chunk:
        return "Could not isolate the specific error in the provided logs."

    print(
        f"🔀 cascadeflow: Error found. Routing chunk to {reasoning_model} "
        "for deep reasoning..."
    )

    # 2. Heavy Reasoning Routing: Generate the fix
    system_prompt = (
        "You are an expert DevOps AI. Analyze the provided failed CI/CD log "
        "chunk. Identify the root cause of the failure and provide a clear, "
        "actionable fix. Format your output in Markdown, ready to be posted "
        "as a GitHub PR comment."
    )

    response = get_groq_client().chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Failed Log Chunk:\n\n{error_chunk}"},
        ],
        model=reasoning_model,
        temperature=0.2,
        max_tokens=1024,
    )

    return response.choices[0].message.content
