"""
Deterministic policy engine.

This is the one place that decides whether an agent-proposed action may
proceed. It reads merchant-configured limits from the `policies` table and
applies plain if/else logic — no LLM call happens anywhere in this file,
and it never will. Per the project's core principle:

    LLM proposes. Data systems provide facts. Deterministic policies
    authorize. Humans approve consequential actions. Razorpay executes
    approved transactions. Audit logs record everything.

Fail-safe default: if an action type is unrecognized, or a required policy
value is missing/unparseable, the engine BLOCKs rather than guesses. An
unknown situation must never silently become an approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum

from sqlalchemy.orm import Session

from app.models.governance import Policy


class PolicyDecision(str, Enum):
    APPROVE = "approve"                  # may execute with no human step
    REQUIRES_APPROVAL = "requires_approval"  # within limits, still needs a human
    BLOCK = "block"                      # exceeds a hard limit — never executes


@dataclass
class PolicyResult:
    decision: PolicyDecision
    reason: str
    policy_id: str


# Merchant-configurable limits this engine understands. Keys match exactly
# what app/db/seed.py inserts into the `policies` table.
_DEFAULTS = {
    "max_discount_percent": "15",
    "max_campaign_budget": "50000",
    "max_autonomous_transaction_amount": "0",
    "max_refund_amount_autonomous": "0",
}


def _get_policy_decimal(db: Session, merchant_id: str, key: str) -> Decimal:
    """
    Reads one policy value for a merchant. Falls back to a conservative
    built-in default (never a permissive one) if the merchant hasn't
    configured it — a missing policy row must never mean "no limit".
    """
    row = (
        db.query(Policy)
        .filter(Policy.merchant_id == merchant_id, Policy.key == key)
        .first()
    )
    raw = row.value if row is not None else _DEFAULTS.get(key)
    if raw is None:
        raise ValueError(f"No policy value or default available for '{key}'")
    try:
        return Decimal(str(raw))
    except InvalidOperation as e:
        raise ValueError(f"Policy '{key}' has a non-numeric value: {raw!r}") from e


def evaluate_discount_campaign(
    db: Session, merchant_id: str, *, discount_percent: float, budget: float | None = None
) -> PolicyResult:
    """
    Discount campaigns (upsell/cross-sell/bundle with a % off). Two hard
    limits (discount %, budget) can BLOCK outright; anything within limits
    still REQUIRES_APPROVAL — campaigns are never fully autonomous here.
    """
    max_discount = _get_policy_decimal(db, merchant_id, "max_discount_percent")
    if Decimal(str(discount_percent)) > max_discount:
        return PolicyResult(
            decision=PolicyDecision.BLOCK,
            reason=f"Proposed discount {discount_percent}% exceeds merchant policy maximum of {max_discount}%.",
            policy_id="max_discount_percent",
        )

    if budget is not None:
        max_budget = _get_policy_decimal(db, merchant_id, "max_campaign_budget")
        if Decimal(str(budget)) > max_budget:
            return PolicyResult(
                decision=PolicyDecision.BLOCK,
                reason=f"Proposed campaign budget {budget} exceeds merchant policy maximum of {max_budget}.",
                policy_id="max_campaign_budget",
            )

    return PolicyResult(
        decision=PolicyDecision.REQUIRES_APPROVAL,
        reason="Within discount/budget limits. Human approval is required for all campaigns.",
        policy_id="max_discount_percent",
    )


def evaluate_non_discount_campaign(db: Session, merchant_id: str) -> PolicyResult:
    """
    Upsell/cross-sell campaigns with no discount attached still change
    what customers see and still cost merchant effort/inventory
    commitment — always requires approval, never auto-blocked (no
    financial limit applies), never auto-approved.
    """
    return PolicyResult(
        decision=PolicyDecision.REQUIRES_APPROVAL,
        reason="Campaign creation always requires human approval, regardless of financial exposure.",
        policy_id="require_approval_for_all_campaigns",
    )


def evaluate_transaction(db: Session, merchant_id: str, *, amount: float) -> PolicyResult:
    """
    Order/payment creation (dashboard-triggered or AI-buyer-triggered).
    Default policy (max_autonomous_transaction_amount = 0) means every
    transaction requires approval unless a merchant explicitly raises
    this limit above 0.
    """
    max_autonomous = _get_policy_decimal(db, merchant_id, "max_autonomous_transaction_amount")
    if Decimal(str(amount)) > max_autonomous:
        return PolicyResult(
            decision=PolicyDecision.REQUIRES_APPROVAL,
            reason=f"Transaction amount {amount} exceeds the autonomous execution threshold of {max_autonomous}.",
            policy_id="max_autonomous_transaction_amount",
        )
    return PolicyResult(
        decision=PolicyDecision.APPROVE,
        reason=f"Transaction amount {amount} is within the autonomous execution threshold.",
        policy_id="max_autonomous_transaction_amount",
    )


def evaluate_refund(db: Session, merchant_id: str, *, amount: float) -> PolicyResult:
    """Refunds — same shape as transactions, separate (usually stricter) limit."""
    max_autonomous = _get_policy_decimal(db, merchant_id, "max_refund_amount_autonomous")
    if Decimal(str(amount)) > max_autonomous:
        return PolicyResult(
            decision=PolicyDecision.REQUIRES_APPROVAL,
            reason=f"Refund amount {amount} exceeds the autonomous refund threshold of {max_autonomous}.",
            policy_id="max_refund_amount_autonomous",
        )
    return PolicyResult(
        decision=PolicyDecision.APPROVE,
        reason=f"Refund amount {amount} is within the autonomous refund threshold.",
        policy_id="max_refund_amount_autonomous",
    )


def evaluate_action(db: Session, merchant_id: str, action_type: str, payload: dict) -> PolicyResult:
    """
    Single entry point the agent/executor calls. Dispatches by action_type;
    anything unrecognized BLOCKs rather than guessing — see module docstring
    on fail-safe defaults.
    """
    if action_type in ("create_discount_campaign", "create_bundle_campaign"):
        return evaluate_discount_campaign(
            db,
            merchant_id,
            discount_percent=payload.get("discount_percent", 0),
            budget=payload.get("budget"),
        )
    if action_type in ("create_upsell_campaign", "create_cross_sell_campaign"):
        return evaluate_non_discount_campaign(db, merchant_id)
    if action_type in ("create_order", "create_transaction"):
        return evaluate_transaction(db, merchant_id, amount=payload.get("amount", 0))
    if action_type == "create_refund":
        return evaluate_refund(db, merchant_id, amount=payload.get("amount", 0))

    return PolicyResult(
        decision=PolicyDecision.BLOCK,
        reason=f"Unrecognized action type '{action_type}'. Unknown actions are blocked by default.",
        policy_id="unknown_action_type",
    )