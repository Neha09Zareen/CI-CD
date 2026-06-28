# src/agent.py
"""LangGraph CI/CD agent.

Defines the state graph that drives failure analysis: chunk the logs,
recall any historical fix from Hindsight, route through cascadeflow to
generate a fix, then retain the new fix for next time.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from hindsight import recall_historical_fix, retain_successful_fix
from cascade_parser import cascade_chunk_logs
from cascade_flow import analyze_logs_with_cascadeflow


class CIState(TypedDict, total=False):
    """State passed between graph nodes."""

    run_id: int
    repo_name: str
    raw_logs: str
    chunks: list[str]
    historical_context: str
    suggested_fix: str


def chunk_logs_node(state: CIState):
    """Pre-parse raw logs into overlapping chunks via Cascade."""
    print("✂️ [Node] Cascade - Chunking logs...")
    chunks = cascade_chunk_logs(state.get("raw_logs", ""))
    return {"chunks": chunks}


def hindsight_recall_node(state: CIState):
    print("🧠 [Node] Hindsight Recall - Checking memory...")
    # We'll use the first chunk as a proxy for the error signature
    first_chunk = state["chunks"][0] if state["chunks"] else ""
    past_fix = recall_historical_fix(first_chunk)
    if past_fix:
        print("🎯 Hindsight: Found a historical fix!")
        return {"historical_context": past_fix}
    return {"historical_context": "No historical fix found."}


def cascadeflow_node(state: CIState):
    """Route chunks through cascadeflow to generate a fix."""
    print("🔀 [Node] cascadeflow - Routing chunks for analysis...")
    fix = analyze_logs_with_cascadeflow(state.get("chunks", []))
    return {"suggested_fix": fix}


def hindsight_retain_node(state: CIState):
    print("💾 [Node] Hindsight Retain - Saving to memory...")
    if state["suggested_fix"] and state["chunks"]:
        # Use the first chunk as the signature (must match recall's key)
        signature = state["chunks"][0]
        retain_successful_fix(signature, state["suggested_fix"])
    return {}


def build_agent():
    """Compile and return the LangGraph CI/CD agent."""
    graph = StateGraph(CIState)
    graph.add_node("chunk_logs", chunk_logs_node)
    graph.add_node("hindsight_recall", hindsight_recall_node)
    graph.add_node("cascadeflow", cascadeflow_node)
    graph.add_node("hindsight_retain", hindsight_retain_node)

    graph.add_edge(START, "chunk_logs")
    graph.add_edge("chunk_logs", "hindsight_recall")
    graph.add_edge("hindsight_recall", "cascadeflow")
    graph.add_edge("cascadeflow", "hindsight_retain")
    graph.add_edge("hindsight_retain", END)

    return graph.compile()
