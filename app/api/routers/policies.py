"""app/api/routers/policies.py"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import PolicyOut, PolicyUpdateRequest
from app.db.base import get_db
from app.models.governance import Policy

router = APIRouter(prefix="/policies", tags=["policies"])


@router.get("", response_model=list[PolicyOut])
def list_policies(merchant_id: str, db: Session = Depends(get_db)):
    return db.query(Policy).filter(Policy.merchant_id == merchant_id).all()


@router.put("", response_model=PolicyOut)
def upsert_policy(merchant_id: str, payload: PolicyUpdateRequest, db: Session = Depends(get_db)):
    row = (
        db.query(Policy)
        .filter(Policy.merchant_id == merchant_id, Policy.key == payload.key)
        .first()
    )
    if row is None:
        row = Policy(merchant_id=merchant_id, key=payload.key, value=str(payload.value))
        db.add(row)
    else:
        row.value = str(payload.value)
    db.commit()
    db.refresh(row)
    return row