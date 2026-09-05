"""
Buyer-facing purchase flow — the entry point an EXTERNAL AI buyer (not the
merchant) uses to actually transact with a merchant's store. Deliberately
NOT routed through the LLM agent: an explicit "buy product X, quantity Y"
request has no ambiguity to reason about, so it goes straight to the same
deterministic policy engine every other money action in this codebase
uses. A purchase is never auto-executed just because it came from a buyer
rather than the merchant's own agent.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.agents.audit import log_event
from app.models.commerce import Order, OrderItem
from app.models.governance import AgentRun, Approval
from app.policies import policy_engine
from app.services import catalog_service


class PurchaseError(Exception):
    """Raised for invalid purchase requests (product not found, out of
    stock, bad quantity) — never for policy decisions, which are a normal
    PurchaseResult, not an exception."""


@dataclass
class PurchaseResult:
    status: str  # "requires_approval" | "blocked"
    order_id: str | None
    approval_id: str | None
    reason: str


def initiate_purchase(
    db: Session,
    merchant_id: str,
    product_id: str,
    quantity: int,
    *,
    customer_email: str | None = None,
) -> PurchaseResult:
    if quantity < 1:
        raise PurchaseError("quantity must be at least 1.")

    product = catalog_service.get_product(db, merchant_id, product_id)
    if product is None:
        raise PurchaseError(f"No product found with id {product_id!r} for this merchant.")

    availability = catalog_service.check_availability(db, merchant_id, product_id, quantity)
    if not availability["available"]:
        raise PurchaseError(f"Product unavailable: {availability['reason']}")

    amount = float(product.price) * quantity

    # Every governed action needs an AgentRun to hang its Approval/AuditLog
    # rows off — the same durable-trail requirement runner.py enforces for
    # LLM-initiated actions. request_text documents this came from an AI
    # buyer, not the merchant, so the audit trail stays honest about who
    # asked for what.
    agent_run = AgentRun(
        merchant_id=merchant_id,
        request_text=f"[ai_buyer] purchase request: {quantity}x product {product_id}",
        intent="buyer_purchase",
        status="running",
    )
    db.add(agent_run)
    db.flush()

    log_event(
        db,
        merchant_id=merchant_id,
        run_id=agent_run.id,
        event_type="request_received",
        summary=f"AI buyer requested purchase: {quantity}x {product.name}.",
        detail={"product_id": product_id, "quantity": quantity, "customer_email": customer_email},
    )

    policy_result = policy_engine.evaluate_action(db, merchant_id, "create_order", {"amount": amount})

    log_event(
        db,
        merchant_id=merchant_id,
        run_id=agent_run.id,
        event_type="policy_checked",
        summary=policy_result.reason,
        detail={"decision": policy_result.decision.value, "policy_id": policy_result.policy_id},
    )

    order = Order(
        merchant_id=merchant_id,
        customer_id=None,  # AI buyers aren't necessarily an existing Customer row
        status="created",
        total_amount=amount,
        currency=product.currency,
        source="ai_buyer",
        idempotency_key=str(uuid.uuid4()),
    )
    db.add(order)
    db.flush()
    db.add(OrderItem(order_id=order.id, product_id=product.id, quantity=quantity, unit_price=product.price))

    if policy_result.decision == policy_engine.PolicyDecision.BLOCK:
        order.status = "cancelled"
        agent_run.status = "blocked"
        agent_run.completed_at = datetime.utcnow()
        log_event(
            db,
            merchant_id=merchant_id,
            run_id=agent_run.id,
            event_type="action_blocked",
            summary=policy_result.reason,
            detail={"order_id": order.id},
        )
        db.commit()
        return PurchaseResult(status="blocked", order_id=order.id, approval_id=None, reason=policy_result.reason)

    # APPROVE and REQUIRES_APPROVAL both land here today: with the seeded
    # default (max_autonomous_transaction_amount = 0), every real amount
    # requires approval, so only that path is exercised in practice. If a
    # merchant later raises that limit, APPROVE would need its own
    # "execute immediately" branch — deliberately not built yet, so
    # raising a policy number alone can never silently enable autonomous
    # execution before that branch exists and is tested.
    approval = Approval(
        run_id=agent_run.id,
        merchant_id=merchant_id,
        action_type="create_order",
        action_payload={
            "type": "create_order",
            "order_id": order.id,
            "product_id": product_id,
            "quantity": quantity,
            "amount": amount,
            "currency": product.currency,
            "customer_email": customer_email,
        },
        reason=f"AI buyer requested {quantity}x {product.name}.",
        expected_impact=f"Order total: {amount} {product.currency}.",
        risk_level="medium" if amount >= 10000 else "low",
        policy_result="pass",
        policy_detail=policy_result.reason,
        status="pending",
    )
    db.add(approval)
    order.status = "payment_pending"
    agent_run.status = "awaiting_approval"
    db.flush()

    log_event(
        db,
        merchant_id=merchant_id,
        run_id=agent_run.id,
        event_type="approval_requested",
        summary=f"Approval requested for create_order (order {order.id}).",
        detail=approval.action_payload,
    )
    db.commit()

    return PurchaseResult(
        status="requires_approval", order_id=order.id, approval_id=approval.id, reason=policy_result.reason,
    )