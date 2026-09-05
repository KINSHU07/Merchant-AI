"""
Approval decision workflow.

This is the "Human Approval" -> "Action Executor" stage of the pipeline:

    Approval (pending) -> human decides -> [executor runs] -> Audit Log

Two hard rules enforced here:

1. An approval can only be decided ONCE. Re-deciding an already-decided
   approval is rejected outright — this is the "approval replay" /
   "stale approval" protection the project's safety principles call for.
   There is no code path that re-executes an already-approved action.

2. Execution never claims success it didn't earn. Campaign creation and
   order creation (via Razorpay test mode) are fully implemented and do
   real work. Refunds still require additional Razorpay integration work
   and are explicitly logged as NOT EXECUTED, never silently marked
   completed. See "No Fake Functionality" — a fake success message is
   worse than an honest failure message.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.agents.audit import log_event
from app.models.campaigns import Campaign, CampaignEvent
from app.models.commerce import Order
from app.models.governance import AgentRun, Approval
from app.services import razorpay_service

CAMPAIGN_ACTION_TYPES = {
    "create_discount_campaign": "discount",
    "create_bundle_campaign": "bundle",
    "create_upsell_campaign": "upsell",
    "create_cross_sell_campaign": "cross_sell",
}

# Action types the policy engine already knows how to gate, but this
# codebase has no executor for yet. Listed explicitly so a new action type
# reaching here with no handler fails loudly instead of silently doing
# nothing. create_order now has a real executor (see _execute_order) —
# only refunds/manual transactions remain unbuilt.
NOT_YET_EXECUTABLE_ACTION_TYPES = {"create_transaction", "create_refund"}


class ApprovalError(Exception):
    """Raised for invalid approval-decision requests (not found, already decided)."""


def _get_pending_approval(db: Session, approval_id: str) -> Approval:
    approval = db.query(Approval).filter(Approval.id == approval_id).first()
    if approval is None:
        raise ApprovalError(f"No approval found with id {approval_id!r}.")
    if approval.status != "pending":
        raise ApprovalError(
            f"Approval {approval_id!r} has already been decided (status={approval.status!r}, "
            f"decided_at={approval.decided_at}). An approval can only be decided once."
        )
    return approval


def _update_agent_run_status(db: Session, run_id: str | None, status: str) -> None:
    if run_id is None:
        return
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if run is not None:
        run.status = status
        run.completed_at = datetime.utcnow()


def _execute_campaign(db: Session, approval: Approval) -> Campaign:
    payload = approval.action_payload or {}
    campaign_type = CAMPAIGN_ACTION_TYPES.get(payload.get("type"), "discount")

    campaign = Campaign(
        merchant_id=approval.merchant_id,
        type=campaign_type,
        name=payload.get("name") or f"{campaign_type.title()} Campaign",
        target_category=payload.get("target_category"),
        target_product_ids=payload.get("product_ids") or [],
        discount_percent=payload.get("discount_percent"),
        status="active",
        reason=approval.reason,
        expected_impact=approval.expected_impact,
    )
    db.add(campaign)
    db.flush()

    db.add(
        CampaignEvent(
            campaign_id=campaign.id,
            event_type="created",
            detail={"approval_id": approval.id, "source_action": payload.get("type")},
        )
    )
    return campaign


def _execute_order(db: Session, approval: Approval) -> Order:
    """
    Real execution for an approved buyer purchase: creates the actual
    Razorpay test-mode order and updates the Order row this approval was
    raised for. Raises if the Order row is missing (should never happen —
    order_service.initiate_purchase always creates it before the approval)
    or if Razorpay isn't configured; both are real failures, handled by
    the caller as an honest NOT EXECUTED, never silently swallowed.
    """
    payload = approval.action_payload or {}
    order_id = payload.get("order_id")
    order = db.query(Order).filter(Order.id == order_id, Order.merchant_id == approval.merchant_id).first()
    if order is None:
        raise ValueError(f"Approval {approval.id} references missing order {order_id!r}.")

    rzp_order = razorpay_service.create_order(
        amount=float(order.total_amount),
        currency=order.currency,
        receipt=order.id,
        notes={"merchant_id": approval.merchant_id, "approval_id": approval.id},
    )
    order.razorpay_order_id = rzp_order["id"]
    order.status = "payment_pending"  # buyer still has to complete payment via Razorpay checkout
    return order


def approve_approval(db: Session, approval_id: str) -> Approval:
    approval = _get_pending_approval(db, approval_id)
    approval.status = "approved"
    approval.decided_at = datetime.utcnow()

    log_event(
        db,
        merchant_id=approval.merchant_id,
        run_id=approval.run_id,
        event_type="approval_decided",
        summary=f"Approval {approval_id} APPROVED for action '{approval.action_type}'.",
        detail={"action_type": approval.action_type},
    )

    action_type = (approval.action_payload or {}).get("type")

    if action_type in CAMPAIGN_ACTION_TYPES:
        campaign = _execute_campaign(db, approval)
        log_event(
            db,
            merchant_id=approval.merchant_id,
            run_id=approval.run_id,
            event_type="action_executed",
            summary=f"Campaign '{campaign.name}' created and set to active.",
            detail={"campaign_id": campaign.id, "campaign_type": campaign.type},
        )
        _update_agent_run_status(db, approval.run_id, "completed")

    elif action_type == "create_order":
        try:
            order = _execute_order(db, approval)
            log_event(
                db,
                merchant_id=approval.merchant_id,
                run_id=approval.run_id,
                event_type="action_executed",
                summary=(
                    f"Razorpay order {order.razorpay_order_id} created for order {order.id} "
                    f"(amount {order.total_amount} {order.currency}). Awaiting buyer payment."
                ),
                detail={"order_id": order.id, "razorpay_order_id": order.razorpay_order_id},
            )
            _update_agent_run_status(db, approval.run_id, "completed")
        except razorpay_service.RazorpayNotConfigured as e:
            log_event(
                db,
                merchant_id=approval.merchant_id,
                run_id=approval.run_id,
                event_type="action_failed",
                summary=f"Approval granted for 'create_order', but Razorpay is not configured. NOT EXECUTED: {e}",
                detail={"action_type": action_type},
            )
            _update_agent_run_status(db, approval.run_id, "approved_not_executed")
        except Exception as e:
            log_event(
                db,
                merchant_id=approval.merchant_id,
                run_id=approval.run_id,
                event_type="action_failed",
                summary=f"Approval granted for 'create_order', but execution failed: {e}. NOT EXECUTED.",
                detail={"action_type": action_type},
            )
            _update_agent_run_status(db, approval.run_id, "approved_not_executed")

    elif action_type in NOT_YET_EXECUTABLE_ACTION_TYPES:
        log_event(
            db,
            merchant_id=approval.merchant_id,
            run_id=approval.run_id,
            event_type="action_failed",
            summary=(
                f"Approval granted for '{action_type}', but no executor exists yet for this "
                f"action type (Razorpay integration not implemented). NOT EXECUTED."
            ),
            detail={"action_type": action_type},
        )
        _update_agent_run_status(db, approval.run_id, "approved_not_executed")

    else:
        log_event(
            db,
            merchant_id=approval.merchant_id,
            run_id=approval.run_id,
            event_type="action_failed",
            summary=f"Approved action type '{action_type}' has no known executor. NOT EXECUTED.",
            detail={"action_type": action_type},
        )
        _update_agent_run_status(db, approval.run_id, "approved_not_executed")

    db.commit()
    return approval


def reject_approval(db: Session, approval_id: str, reason: str | None = None) -> Approval:
    approval = _get_pending_approval(db, approval_id)
    approval.status = "rejected"
    approval.decided_at = datetime.utcnow()

    log_event(
        db,
        merchant_id=approval.merchant_id,
        run_id=approval.run_id,
        event_type="approval_decided",
        summary=f"Approval {approval_id} REJECTED for action '{approval.action_type}'."
        + (f" Reason: {reason}" if reason else ""),
        detail={"action_type": approval.action_type, "rejection_reason": reason},
    )
    _update_agent_run_status(db, approval.run_id, "rejected")

    # A rejected buyer order must not stay stuck in "payment_pending" —
    # mark it cancelled so it never silently lingers inconsistent with
    # its approval's outcome.
    if approval.action_type == "create_order":
        payload = approval.action_payload or {}
        order = db.query(Order).filter(Order.id == payload.get("order_id")).first()
        if order is not None:
            order.status = "cancelled"

    db.commit()
    return approval