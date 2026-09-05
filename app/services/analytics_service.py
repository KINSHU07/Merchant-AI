"""
Deterministic analytics — every number here comes from SQL aggregation,
never from the LLM. The agent's analytics_tool calls these functions and
hands the LLM the *results* to explain; the LLM never computes a metric
itself. This is what "LLM proposes, data systems provide facts" means in
practice.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import combinations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.commerce import Order, OrderItem, Product


def _paid_orders_query(db: Session, merchant_id: str, since: datetime | None = None, until: datetime | None = None):
    q = db.query(Order).filter(Order.merchant_id == merchant_id, Order.status == "paid")
    if since:
        q = q.filter(Order.created_at >= since)
    if until:
        q = q.filter(Order.created_at < until)
    return q


def revenue_summary(db: Session, merchant_id: str, period_days: int = 30) -> dict:
    """Revenue, order count, AOV for the last N days vs the N days before that."""
    now = datetime.utcnow()
    current_start = now - timedelta(days=period_days)
    previous_start = now - timedelta(days=period_days * 2)

    current_orders = _paid_orders_query(db, merchant_id, since=current_start).all()
    previous_orders = _paid_orders_query(db, merchant_id, since=previous_start, until=current_start).all()

    current_revenue = float(sum(Decimal(str(o.total_amount)) for o in current_orders))
    previous_revenue = float(sum(Decimal(str(o.total_amount)) for o in previous_orders))

    growth_pct = None
    if previous_revenue > 0:
        growth_pct = round(((current_revenue - previous_revenue) / previous_revenue) * 100, 2)

    aov = round(current_revenue / len(current_orders), 2) if current_orders else 0.0

    # payment health — surfaces failure-rate anomalies deterministically
    all_recent = db.query(Order).filter(Order.merchant_id == merchant_id, Order.created_at >= current_start).all()
    failed = [o for o in all_recent if o.status == "failed"]
    failure_rate = round(len(failed) / len(all_recent) * 100, 2) if all_recent else 0.0

    return {
        "period_days": period_days,
        "current_revenue": current_revenue,
        "previous_revenue": previous_revenue,
        "revenue_growth_percent": growth_pct,
        "order_count": len(current_orders),
        "average_order_value": aov,
        "payment_failure_rate_percent": failure_rate,
    }


def top_products(db: Session, merchant_id: str, period_days: int = 30, limit: int = 5) -> list[dict]:
    """Best-selling products by revenue in the period, from order_items — authoritative."""
    since = datetime.utcnow() - timedelta(days=period_days)
    rows = (
        db.query(
            Product.id,
            Product.name,
            Product.category,
            func.sum(OrderItem.quantity).label("units_sold"),
            func.sum(OrderItem.quantity * OrderItem.unit_price).label("revenue"),
        )
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.merchant_id == merchant_id, Order.status == "paid", Order.created_at >= since)
        .group_by(Product.id, Product.name, Product.category)
        .order_by(func.sum(OrderItem.quantity * OrderItem.unit_price).desc())
        .limit(limit)
        .all()
    )
    return [
        {"product_id": r.id, "name": r.name, "category": r.category, "units_sold": int(r.units_sold), "revenue": float(r.revenue)}
        for r in rows
    ]


def low_performing_products(db: Session, merchant_id: str, period_days: int = 30, limit: int = 5) -> list[dict]:
    """
    Active products with the lowest (or zero) sales in the period — includes
    products with no orders at all, which a pure 'top N ascending' query
    would miss since they'd never appear in order_items.
    """
    since = datetime.utcnow() - timedelta(days=period_days)
    sold_subq = (
        db.query(OrderItem.product_id, func.sum(OrderItem.quantity).label("units_sold"))
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.merchant_id == merchant_id, Order.status == "paid", Order.created_at >= since)
        .group_by(OrderItem.product_id)
        .subquery()
    )
    rows = (
        db.query(Product.id, Product.name, Product.category, func.coalesce(sold_subq.c.units_sold, 0).label("units_sold"))
        .outerjoin(sold_subq, sold_subq.c.product_id == Product.id)
        .filter(Product.merchant_id == merchant_id, Product.is_active.is_(True))
        .order_by(func.coalesce(sold_subq.c.units_sold, 0).asc())
        .limit(limit)
        .all()
    )
    return [{"product_id": r.id, "name": r.name, "category": r.category, "units_sold": int(r.units_sold)} for r in rows]


def product_pair_frequency(db: Session, merchant_id: str, period_days: int = 90, min_count: int = 3, limit: int = 10) -> list[dict]:
    """
    Which product pairs are frequently bought in the same order — the
    factual basis for cross-sell/bundle recommendations. Computed in Python
    over order groupings rather than a giant self-join, since order baskets
    are small (1-3 items) and this is far more readable than SQL for
    combination counting.
    """
    since = datetime.utcnow() - timedelta(days=period_days)
    rows = (
        db.query(OrderItem.order_id, Product.id, Product.name)
        .join(Product, Product.id == OrderItem.product_id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.merchant_id == merchant_id, Order.status == "paid", Order.created_at >= since)
        .all()
    )
    baskets: dict[str, list[tuple[str, str]]] = {}
    for order_id, product_id, name in rows:
        baskets.setdefault(order_id, []).append((product_id, name))

    pair_counts: Counter = Counter()
    for items in baskets.values():
        unique_items = sorted(set(items))
        for (id_a, name_a), (id_b, name_b) in combinations(unique_items, 2):
            pair_counts[(id_a, name_a, id_b, name_b)] += 1

    results = [
        {"product_a_id": a_id, "product_a_name": a_name, "product_b_id": b_id, "product_b_name": b_name, "co_occurrence_count": count}
        for (a_id, a_name, b_id, b_name), count in pair_counts.items()
        if count >= min_count
    ]
    results.sort(key=lambda r: r["co_occurrence_count"], reverse=True)
    return results[:limit]

def recent_orders(db: Session, merchant_id: str, limit: int = 5) -> list[dict]:
    """
    Most recent paid orders — the ONLY factual basis a refund proposal may
    be grounded in. The LLM must copy order_id/total_amount from here
    verbatim; it is never allowed to invent either. See
    agents/nodes.py::_validate_proposed_action, which enforces this.
    """
    rows = (
        db.query(Order)
        .filter(Order.merchant_id == merchant_id, Order.status == "paid")
        .order_by(Order.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {"order_id": o.id, "total_amount": float(o.total_amount), "created_at": o.created_at.isoformat()}
        for o in rows
    ]

def category_trend(db: Session, merchant_id: str, category: str, period_days: int = 30) -> dict:
    """Revenue for one category, current vs previous period — used to confirm/refute a suspected trend before recommending action on it."""
    now = datetime.utcnow()
    current_start = now - timedelta(days=period_days)
    previous_start = now - timedelta(days=period_days * 2)

    def _category_revenue(since, until):
        rows = (
            db.query(func.sum(OrderItem.quantity * OrderItem.unit_price))
            .join(Order, Order.id == OrderItem.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .filter(
                Order.merchant_id == merchant_id,
                Order.status == "paid",
                Product.category == category,
                Order.created_at >= since,
                Order.created_at < until,
            )
            .scalar()
        )
        return float(rows or 0)

    current = _category_revenue(current_start, now)
    previous = _category_revenue(previous_start, current_start)
    change_pct = round(((current - previous) / previous) * 100, 2) if previous > 0 else None

    return {"category": category, "current_revenue": current, "previous_revenue": previous, "change_percent": change_pct}