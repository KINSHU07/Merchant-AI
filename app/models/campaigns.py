from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import PortableJSON


def _uuid() -> str:
    return str(uuid.uuid4())


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # upsell | cross_sell | bundle | discount
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_category: Mapped[str] = mapped_column(String(120), nullable=True)
    target_product_ids: Mapped[list] = mapped_column(PortableJSON, default=list)
    discount_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft | active | rejected | ended
    reason: Mapped[str] = mapped_column(String(1000), nullable=True)
    expected_impact: Mapped[str] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    events: Mapped[list["CampaignEvent"]] = relationship(back_populates="campaign")


class CampaignEvent(Base):
    __tablename__ = "campaign_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)  # created | approved | rejected | executed
    detail: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    campaign: Mapped["Campaign"] = relationship(back_populates="events")