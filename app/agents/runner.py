"""
Agent runner — wraps the compiled graph with persistence.

Every run produces a durable trail: one AgentRun row, one ToolCall row per
tool invocation, an Approval row if (and only if) the policy engine says
the proposed action needs a human, and AuditLog entries at each meaningful
transition. This is what makes a run inspectable after the fact — the
"explainable, bounded and gated" requirement isn't just in the code path,
it's in the database.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.agents.audit import log_event
from app.agents.graph import build_agent_graph
from app.agents.state import AgentState
from app.models.governance import AgentRun, Approval, ToolCall


def _log(db: Session, *, merchant_id: str, run_id: str | None, event_type: str, summary: str, detail: dict | None = None) -> None:
    log_event(db, merchant_id=merchant_id, run_id=run_id, event_type=event_type, summary=summary, detail=detail)


def _risk_level(action: dict) -> str:
    """Simple, explainable heuristic — not a model, just readable rules."""
    discount = action.get("discount_percent") or 0
    budget = action.get("budget") or 0
    if discount >= 15 or budget >= 50000:
        return "high"
    if discount >= 8 or budget >= 10000:
        return "medium"
    return "low"


def run_agent(db: Session, merchant_id: str, request_text: str) -> AgentRun:
    agent_run = AgentRun(merchant_id=merchant_id, request_text=request_text, status="running")
    db.add(agent_run)
    db.flush()  # get agent_run.id before the graph runs

    _log(db, merchant_id=merchant_id, run_id=agent_run.id, event_type="request_received", summary=request_text)
    db.commit()

    graph = build_agent_graph(db)
    initial_state: AgentState = {
        "merchant_id": merchant_id,
        "request_text": request_text,
        "status": "running",
    }

    try:
        final_state: AgentState = graph.invoke(initial_state)
    except Exception as e:
        agent_run.status = "failed"
        agent_run.error = f"Unhandled agent error: {e}"
        agent_run.completed_at = datetime.utcnow()
        _log(db, merchant_id=merchant_id, run_id=agent_run.id, event_type="action_failed", summary=str(e))
        db.commit()
        return agent_run

    # Persist every tool call, success or failure, exactly as it happened.
    for tc in final_state.get("tool_calls", []):
        db.add(
            ToolCall(
                run_id=agent_run.id,
                tool_name=tc["tool_name"],
                input=tc["input"],
                output_summary=tc["output_summary"],
                success=tc["success"],
            )
        )
        _log(
            db,
            merchant_id=merchant_id,
            run_id=agent_run.id,
            event_type="tool_invoked",
            summary=f"{tc['tool_name']} ({'ok' if tc['success'] else 'failed'})",
            detail={"input": tc["input"]},
        )

    agent_run.intent = final_state.get("intent")
    agent_run.recommendation = final_state.get("recommendation")
    agent_run.proposed_action = final_state.get("proposed_action")
    agent_run.error = final_state.get("error")
    status = final_state.get("status", "completed")
    agent_run.status = status
    agent_run.completed_at = datetime.utcnow()

    if agent_run.recommendation:
        _log(
            db,
            merchant_id=merchant_id,
            run_id=agent_run.id,
            event_type="recommendation_made",
            summary=agent_run.recommendation,
            detail={"evidence": final_state.get("evidence", [])},
        )

    if final_state.get("requires_action"):
        policy_decision = final_state.get("policy_decision")
        policy_reason = final_state.get("policy_reason") or ""
        _log(
            db,
            merchant_id=merchant_id,
            run_id=agent_run.id,
            event_type="policy_checked",
            summary=policy_reason,
            detail={"decision": policy_decision, "policy_id": final_state.get("policy_id")},
        )

        action = final_state.get("proposed_action") or {}
        if status == "awaiting_approval":
            db.add(
                Approval(
                    run_id=agent_run.id,
                    merchant_id=merchant_id,
                    action_type=action.get("type", "unknown"),
                    action_payload=action,
                    reason=agent_run.recommendation,
                    expected_impact=final_state.get("expected_impact"),
                    risk_level=_risk_level(action),
                    policy_result="pass",
                    policy_detail=policy_reason,
                    status="pending",
                )
            )
            _log(
                db,
                merchant_id=merchant_id,
                run_id=agent_run.id,
                event_type="approval_requested",
                summary=f"Approval requested for {action.get('type')}",
                detail=action,
            )
        elif status == "blocked":
            _log(
                db,
                merchant_id=merchant_id,
                run_id=agent_run.id,
                event_type="action_blocked",
                summary=policy_reason,
                detail=action,
            )

    if status == "failed" and agent_run.error:
        _log(db, merchant_id=merchant_id, run_id=agent_run.id, event_type="action_failed", summary=agent_run.error)

    db.commit()
    return agent_run