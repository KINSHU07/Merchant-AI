"""
Governance/observability tables. This is the backbone of the judging bar:
"every money action must be explainable, bounded and gated."

agent_runs     -> one row per user request handled by the LangGraph agent
tool_calls     -> every tool invocation within a run (analytics, catalog, RAG...)
policies       -> merchant-configurable limits, read by the policy engine
approvals      -> consequential actions awaiting/receiving merchant decision
audit_logs     -> immutable record of the full decision chain for a run
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import PortableJSON


def _uuid() -> str:
    return str(uuid.uuid4())


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True, nullable=False)
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    # running | completed | failed | awaiting_approval
    recommendation: Mapped[str] = mapped_column(Text, nullable=True)
    proposed_action: Mapped[dict] = mapped_column(PortableJSON, nullable=True)
    error: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    tool_calls: Mapped[list["ToolCall"]] = relationship(back_populates="run")


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    output_summary: Mapped[str] = mapped_column(Text, nullable=True)
    # Full output stored separately if large; summary kept here for audit UI.
    latency_ms: Mapped[int] = mapped_column(String(16), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    run: Mapped["AgentRun"] = relationship(back_populates="tool_calls")


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    # e.g. max_discount_percent, max_campaign_budget, max_transaction_amount
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True, nullable=False)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True, nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    action_payload: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    expected_impact: Mapped[str] = mapped_column(String(500), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")  # low | medium | high
    policy_result: Mapped[str] = mapped_column(String(16), default="pending")  # pass | fail | pending
    policy_detail: Mapped[str] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | rejected
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True, nullable=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # request_received | tool_invoked | recommendation_made | policy_checked |
    # approval_requested | approval_decided | action_executed | action_failed
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    razorpay_reference: Mapped[str] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)