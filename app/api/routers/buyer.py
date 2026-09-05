"""
Buyer-facing routes — the entry point an EXTERNAL AI buyer (not the
merchant) uses to discover products and place orders. Deliberately
separate from app/api/routers/agent.py: that surface is for the merchant
managing their own store; this one is for someone else transacting with
it. Every purchase here still goes through the same policy engine and
human-approval gate as every other money action in this codebase.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.api.schemas import BuyerProductOut, BuyerPurchaseRequest, BuyerPurchaseResponse
from app.db.base import get_db
from app.models.commerce import Merchant
from app.services import catalog_service
from app.services.order_service import PurchaseError, initiate_purchase

router = APIRouter(prefix="/buyer", tags=["buyer"])


@router.get("/catalog/{merchant_id}", response_model=list[BuyerProductOut])
def get_catalog(
    merchant_id: str = Path(...),
    category: str | None = Query(default=None),
    query: str | None = Query(default=None),
    in_stock_only: bool = Query(default=True),
    limit: int = Query(default=20, le=50),
    db: Session = Depends(get_db),
):
    merchant = db.query(Merchant).filter(Merchant.id == merchant_id).first()
    if merchant is None:
        raise HTTPException(404, f"No merchant found with id {merchant_id!r}.")

    products = catalog_service.search_products(
        db, merchant_id, query=query, category=category, in_stock_only=in_stock_only, limit=limit,
    )
    return [catalog_service.to_ai_readable(p, merchant.name) for p in products]


@router.post("/orders/{merchant_id}", response_model=BuyerPurchaseResponse, status_code=201)
def place_order(
    payload: BuyerPurchaseRequest,
    merchant_id: str = Path(...),
    db: Session = Depends(get_db),
):
    merchant = db.query(Merchant).filter(Merchant.id == merchant_id).first()
    if merchant is None:
        raise HTTPException(404, f"No merchant found with id {merchant_id!r}.")

    try:
        result = initiate_purchase(
            db, merchant_id, payload.product_id, payload.quantity, customer_email=payload.customer_email,
        )
    except PurchaseError as e:
        raise HTTPException(422, str(e)) from e

    return BuyerPurchaseResponse(
        status=result.status, order_id=result.order_id, approval_id=result.approval_id, reason=result.reason,
    )