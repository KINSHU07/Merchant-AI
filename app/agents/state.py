"""
LangGraph agent state.

Deliberately explicit typed fields rather than a single blob of text passed
between nodes — every node reads/writes named keys, so it's obvious what
data is available at each step and the AgentRun/ToolCall audit rows can be
built directly from this state without re-deriving anything.
"""
from __future__ import annotations

from typing import Any, TypedDict


class ToolCallRecord(TypedDict):
    tool_name: str
    input: dict
    output_summary: str
    success: bool


class AgentState(TypedDict, total=False):
    # Input
    merchant_id: str
    request_text: str

    # Set by classify_intent
    intent: str

    # Set by run_tools
    tool_calls: list[ToolCallRecord]
    retrieved_data: dict[str, Any]

    # Set by reason
    recommendation: str
    evidence: list[str]
    requires_action: bool
    proposed_action: dict | None
    expected_impact: str | None

    # Set by check_policy
    policy_decision: str | None  # "approve" | "requires_approval" | "block" | None
    policy_reason: str | None
    policy_id: str | None

    # Set by finalize / on failure
    status: str  # "completed" | "blocked" | "awaiting_approval" | "failed"
    error: str | None