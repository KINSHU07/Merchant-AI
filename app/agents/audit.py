"""Shared audit-log writer. One place, one row shape, used by every part
of the agent system that needs to record what happened and why."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.governance import AuditLog


def log_event(
    db: Session,
    *,
    merchant_id: str,
    run_id: str | None,
    event_type: str,
    summary: str,
    detail: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            merchant_id=merchant_id,
            run_id=run_id,
            event_type=event_type,
            summary=summary,
            detail=detail or {},
        )
    )