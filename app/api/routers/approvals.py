"""app/api/routers/approvals.py"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.agents.executor import ApprovalError, approve_approval, reject_approval
from app.api.schemas import ApprovalOut, ApprovalRejectRequest
from app.db.base import get_db
from app.models.governance import Approval

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalOut])
def list_approvals(
    merchant_id: str,
    status: str | None = Query(default="pending"),
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    q = db.query(Approval).filter(Approval.merchant_id == merchant_id)
    if status:
        q = q.filter(Approval.status == status)
    return q.order_by(Approval.created_at.desc()).limit(limit).all()


@router.get("/{approval_id}", response_model=ApprovalOut)
def get_approval(approval_id: str, db: Session = Depends(get_db)):
    approval = db.query(Approval).filter(Approval.id == approval_id).first()
    if approval is None:
        raise HTTPException(404, f"No approval found with id {approval_id!r}.")
    return approval


@router.post("/{approval_id}/approve", response_model=ApprovalOut)
def approve(approval_id: str, db: Session = Depends(get_db)):
    try:
        return approve_approval(db, approval_id)
    except ApprovalError as e:
        # "already decided" / "not found" are client errors (409/404), not
        # 500s — this is expected, well-typed control flow, not a bug.
        raise HTTPException(409, str(e)) from e


@router.post("/{approval_id}/reject", response_model=ApprovalOut)
def reject(approval_id: str, payload: ApprovalRejectRequest, db: Session = Depends(get_db)):
    try:
        return reject_approval(db, approval_id, reason=payload.reason)
    except ApprovalError as e:
        raise HTTPException(409, str(e)) from e