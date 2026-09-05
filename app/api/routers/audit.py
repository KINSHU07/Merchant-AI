"""app/api/routers/audit.py"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.schemas import AuditLogOut
from app.db.base import get_db
from app.models.governance import AuditLog

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=list[AuditLogOut])
def list_audit_logs(
    merchant_id: str,
    run_id: str | None = None,
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(AuditLog).filter(AuditLog.merchant_id == merchant_id)
    if run_id:
        q = q.filter(AuditLog.run_id == run_id)
    return q.order_by(AuditLog.created_at.desc()).limit(limit).all()