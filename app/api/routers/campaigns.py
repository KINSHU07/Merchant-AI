"""app/api/routers/campaigns.py"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.schemas import CampaignOut
from app.db.base import get_db
from app.models.campaigns import Campaign

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.get("", response_model=list[CampaignOut])
def list_campaigns(
    merchant_id: str,
    status: str | None = None,
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    q = db.query(Campaign).filter(Campaign.merchant_id == merchant_id)
    if status:
        q = q.filter(Campaign.status == status)
    return q.order_by(Campaign.created_at.desc()).limit(limit).all()