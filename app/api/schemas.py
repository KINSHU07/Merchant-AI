"""
API schemas — the validation boundary between HTTP and the agent/governance
services. Two rules kept deliberately strict here:

1. Every response model is built via `from_attributes=True` from an ORM
   row — never a hand-assembled dict — so a schema change and a model
   change can't silently drift apart.
2. Request models validate anything that reaches an LLM prompt or a
   money-bearing service. `request_text` in particular is the one field
   in this whole app that becomes free-text LLM input, so it gets the
   tightest bounds here.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------- Agent runs ----------

class AgentRunRequest(BaseModel):
    merchant_id: str = Field(..., min_length=1)
    request_text: str = Field(..., min_length=1, max_length=2000)


class ToolCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tool_name: str
    input: dict[str, Any]
    output_summary: str | None
    success: bool
    created_at: datetime


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    merchant_id: str
    request_text: str
    intent: str | None
    status: str
    recommendation: str | None
    proposed_action: dict[str, Any] | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class AgentRunDetailOut(AgentRunOut):
    tool_calls: list[ToolCallOut] = []


# ---------- Approvals ----------

class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    merchant_id: str
    action_type: str
    action_payload: dict[str, Any]
    reason: str | None
    expected_impact: str | None
    risk_level: str
    policy_result: str
    policy_detail: str | None
    status: str
    decided_at: datetime | None
    created_at: datetime


class ApprovalRejectRequest(BaseModel):
    # Only used for rejection — approval takes no body, matching
    # approve_approval(db, approval_id)/reject_approval(db, approval_id, reason)
    # in executor.py exactly. No generic "PATCH status" endpoint: that would
    # reopen the single-decision guarantee _get_pending_approval enforces.
    reason: str | None = Field(default=None, max_length=1000)


# ---------- Audit logs ----------

class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str | None
    merchant_id: str
    event_type: str
    summary: str
    detail: dict[str, Any]
    created_at: datetime


# ---------- Campaigns ----------

class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    merchant_id: str
    type: str
    name: str
    target_category: str | None
    target_product_ids: list[str]
    discount_percent: Decimal | None
    status: str
    reason: str | None
    expected_impact: str | None
    created_at: datetime


# ---------- Policies ----------

class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    merchant_id: str
    key: str
    value: str
    description: str | None
    updated_at: datetime


class PolicyUpdateRequest(BaseModel):
    # Merchants can only ever raise/lower the four known knobs the policy
    # engine reads (policy_engine._DEFAULTS) — never introduce a new key
    # via the API, since evaluate_action() has no code path that reads an
    # arbitrary key. Constraining the enum here means a typo'd key fails
    # validation instead of silently doing nothing.
    key: Literal[
        "max_discount_percent",
        "max_campaign_budget",
        "max_autonomous_transaction_amount",
        "max_refund_amount_autonomous",
    ]
    value: Decimal = Field(..., ge=0)

# ---------- Buyer-facing ----------

class BuyerProductOut(BaseModel):
    product_id: str
    name: str
    description: str
    category: str
    price: float
    currency: str
    availability: Literal["in_stock", "out_of_stock"]
    inventory_count: int
    merchant: dict[str, str]
    sku: str


class BuyerPurchaseRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    quantity: int = Field(default=1, ge=1, le=100)
    customer_email: str | None = Field(default=None, max_length=255)


class BuyerPurchaseResponse(BaseModel):
    status: Literal["requires_approval", "blocked"]
    order_id: str | None
    approval_id: str | None
    reason: str