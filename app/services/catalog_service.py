"""
Catalog service — the ONLY source of product truth the agent and any
external AI buyer are allowed to see. Price, inventory, and availability
always come from here (i.e. from PostgreSQL), never from LLM memory or
invention. Pure SQL/ORM, no LLM calls anywhere in this file.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.commerce import Product


def get_product(db: Session, merchant_id: str, product_id: str) -> Product | None:
    """Fetch a single product, scoped to the merchant (no cross-tenant leaks)."""
    return (
        db.query(Product)
        .filter(Product.id == product_id, Product.merchant_id == merchant_id)
        .first()
    )


def search_products(
    db: Session,
    merchant_id: str,
    *,
    query: str | None = None,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    active_only: bool = True,
    in_stock_only: bool = False,
    limit: int = 20,
) -> list[Product]:
    """
    Structured product search — every filter is a real SQL predicate.
    Backs both the merchant dashboard ("which products should I promote")
    and the AI-buyer discovery flow ("laptop under ₹70,000").
    """
    q = db.query(Product).filter(Product.merchant_id == merchant_id)

    if active_only:
        q = q.filter(Product.is_active.is_(True))
    if in_stock_only:
        q = q.filter(Product.inventory_count > 0)
    if category:
        q = q.filter(func.lower(Product.category) == category.lower())
    if min_price is not None:
        q = q.filter(Product.price >= min_price)
    if max_price is not None:
        q = q.filter(Product.price <= max_price)
    if query:
        like = f"%{query.lower()}%"
        q = q.filter(
            func.lower(Product.name).like(like) | func.lower(Product.description).like(like)
        )

    return q.order_by(Product.name).limit(limit).all()


def list_categories(db: Session, merchant_id: str) -> list[str]:
    rows = (
        db.query(Product.category)
        .filter(Product.merchant_id == merchant_id, Product.is_active.is_(True))
        .distinct()
        .all()
    )
    return sorted(r[0] for r in rows)


def check_availability(db: Session, merchant_id: str, product_id: str, quantity: int = 1) -> dict:
    """
    Authoritative stock check. Callers (agent tools, order creation) must
    call this — never assume availability from a stale search result.
    """
    product = get_product(db, merchant_id, product_id)
    if product is None:
        return {"available": False, "reason": "product_not_found"}
    if not product.is_active:
        return {"available": False, "reason": "product_inactive"}
    if product.inventory_count < quantity:
        return {
            "available": False,
            "reason": "insufficient_stock",
            "requested": quantity,
            "in_stock": product.inventory_count,
        }
    return {"available": True, "in_stock": product.inventory_count}


def to_ai_readable(product: Product, merchant_name: str) -> dict:
    """
    Machine-readable product representation for the AI-buyer catalog
    interface (spec section 5). Predictable shape, no prose, no ambiguity —
    an external AI buyer parses this directly.
    """
    return {
        "product_id": product.id,
        "name": product.name,
        "description": product.description,
        "category": product.category,
        "price": float(product.price),
        "currency": product.currency,
        "availability": "in_stock" if product.inventory_count > 0 else "out_of_stock",
        "inventory_count": product.inventory_count,
        "merchant": {"name": merchant_name},
        "sku": product.sku,
    }