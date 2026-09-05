"""
Graph wiring.

    classify_intent -> run_tools -> reason -> [check_policy] -> END

Conditional edges bail out to END the moment any node reports
status == "failed" — a broken intent classification or a malformed LLM
reasoning output must stop the run, not silently continue with garbage
state. check_policy only runs if reason() decided requires_action=True.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.agents.nodes import classify_intent, make_check_policy, make_run_tools, reason
from app.agents.state import AgentState


def _after_classify(state: AgentState) -> str:
    return "end" if state.get("status") == "failed" else "run_tools"


def _after_reason(state: AgentState) -> str:
    if state.get("status") == "failed":
        return "end"
    return "check_policy" if state.get("requires_action") else "end"


def build_agent_graph(db: Session):
    """Compiles a fresh graph bound to the given DB session."""
    graph = StateGraph(AgentState)

    graph.add_node("classify_intent", classify_intent)
    graph.add_node("run_tools", make_run_tools(db))
    graph.add_node("reason", reason)
    graph.add_node("check_policy", make_check_policy(db))

    graph.set_entry_point("classify_intent")

    graph.add_conditional_edges("classify_intent", _after_classify, {"run_tools": "run_tools", "end": END})
    graph.add_edge("run_tools", "reason")
    graph.add_conditional_edges("reason", _after_reason, {"check_policy": "check_policy", "end": END})
    graph.add_edge("check_policy", END)

    return graph.compile()