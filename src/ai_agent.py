"""AI agent logic built on LangGraph.

Defines a minimal state graph that routes a user request through an
LLM (Groq) and optional tool calls (GitHub). Expand the graph with
additional nodes/edges as the agent gains capabilities.
"""

from __future__ import annotations

import os
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class AgentState(TypedDict, total=False):
    """State passed between graph nodes."""

    input: str
    output: str


def _think(state: AgentState) -> AgentState:
    """Placeholder reasoning node.

    Replace with a real Groq LLM call using GROQ_API_KEY.
    """
    user_input = state.get("input", "")
    return {"output": f"Echo: {user_input}"}


def build_agent():
    """Compile and return the LangGraph agent."""
    graph = StateGraph(AgentState)
    graph.add_node("think", _think)
    graph.add_edge(START, "think")
    graph.add_edge("think", END)
    return graph.compile()


def run_agent(user_input: str) -> str:
    """Run the agent against a single user input and return the result."""
    agent = build_agent()
    result = agent.invoke({"input": user_input})
    return result.get("output", "")
