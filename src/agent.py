# src/agent.py
"""LangGraph CI/CD agent (the Brain).

State graph that drives failure analysis:

    chunk_logs -> hindsight_recall -> (conditional)
                                       ├─ memory hit  -> END (skip the LLM)
                                       └─ miss        -> cascadeflow -> retain -> END

Each node calls an optional ``emit`` callback so its progress can be streamed
to the frontend. On a memory hit the expensive reasoning model is skipped
entirely, which is the core cost-efficiency story.
"""

from __future__ import annotations

from typing import Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .cascade_flow import analyze_logs_with_cascadeflow, select_culprit_chunk
from .cascade_parser import cascade_chunk_logs
from .hindsight import recall_historical_fix, retain_successful_fix, signature_for

# emit(step, status, detail) -> None
EmitFn = Callable[[str, str, Optional[str]], None]


class CIState(TypedDict, total=False):
    """State passed between graph nodes."""

    run_id: int
    repo_name: str
    raw_logs: str
    chunks: list[str]
    culprit: str
    historical_context: str
    suggested_fix: str
    source: str  # memory | generated | none
    model_tier: Optional[str]
    emit: EmitFn


def _emit(state: CIState, step: str, status: str, detail: str | None = None) -> None:
    emit = state.get("emit")
    if emit is not None:
        emit(step, status, detail)


def chunk_logs_node(state: CIState) -> CIState:
    """Pre-parse raw logs into overlapping chunks via Cascade."""
    _emit(state, "chunk", "started")
    chunks = cascade_chunk_logs(state.get("raw_logs", ""))
    culprit = ""
    if chunks:
        _, culprit = select_culprit_chunk(chunks)
    _emit(state, "chunk", "completed", f"{len(chunks)} chunks")
    return {"chunks": chunks, "culprit": culprit}


def hindsight_recall_node(state: CIState) -> CIState:
    """Look for a previously stored fix for this error signature."""
    _emit(state, "recall", "started")
    culprit = state.get("culprit") or ""
    past_fix = recall_historical_fix(culprit)
    if past_fix:
        _emit(state, "recall", "completed", "memory hit")
        return {
            "historical_context": past_fix,
            "suggested_fix": past_fix,
            "source": "memory",
        }
    _emit(state, "recall", "completed", "no prior fix")
    return {"historical_context": "", "source": "none"}


def cascadeflow_node(state: CIState) -> CIState:
    """Route the culprit chunk through cascadeflow to generate a fix."""
    _emit(state, "analyze", "started")
    fix, tier = analyze_logs_with_cascadeflow(state.get("chunks", []))
    _emit(state, "analyze", "completed", f"model tier: {tier}")
    return {"suggested_fix": fix, "source": "generated", "model_tier": tier}


def hindsight_retain_node(state: CIState) -> CIState:
    """Persist a newly generated fix to memory for next time."""
    _emit(state, "retain", "started")
    fix = state.get("suggested_fix", "")
    culprit = state.get("culprit") or ""
    if fix and culprit:
        retain_successful_fix(culprit, fix)
        _emit(state, "retain", "completed", signature_for(culprit)[:40])
    else:
        _emit(state, "retain", "skipped")
    return {}


def _route_after_recall(state: CIState) -> str:
    """Conditional edge: skip the LLM when memory already has a fix."""
    return "hit" if state.get("source") == "memory" else "miss"


def build_agent():
    """Compile and return the LangGraph CI/CD agent."""
    graph = StateGraph(CIState)
    graph.add_node("chunk_logs", chunk_logs_node)
    graph.add_node("hindsight_recall", hindsight_recall_node)
    graph.add_node("cascadeflow", cascadeflow_node)
    graph.add_node("hindsight_retain", hindsight_retain_node)

    graph.add_edge(START, "chunk_logs")
    graph.add_edge("chunk_logs", "hindsight_recall")
    graph.add_conditional_edges(
        "hindsight_recall",
        _route_after_recall,
        {"hit": END, "miss": "cascadeflow"},
    )
    graph.add_edge("cascadeflow", "hindsight_retain")
    graph.add_edge("hindsight_retain", END)

    return graph.compile()


# Compile once at import; reused for every failure.
agent = build_agent()
