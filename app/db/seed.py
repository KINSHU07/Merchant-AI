"""
Seed a demo merchant with realistic data so the app is meaningful the moment
it starts — 30 products across categories, customers, ~9 months of order
history with deliberate patterns (laptop buyers often also buy a mouse/bag,
one category trending down, one trending up) so analytics/cross-sell/
anomaly features have something real to find instead of random noise.

Run with:  PYTHONPATH=. python3 -m app.db.seed
(Must run as a module, not `python3 app/db/seed.py` directly — running it as
a script puts app/db/ first on sys.path, which shadows Python's own stdlib
`types` module with this package's app/db/types.py and breaks every import.)
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from sqlalchemy import inspect

from app.db.base import SessionLocal, engine
import app.models  # noqa: F401 — ensures all tables are registered
from app.models.commerce import Customer, Merchant, Order, OrderItem, Product
from app.models.governance import Policy

random.seed(42)  # reproducible demo data across runs

CATEGORIES: dict[str, list[tuple[str, float]]] = {
    "laptops": [
        ("ProBook 14 i5", 54999), ("ProBook 14 i7", 68999), ("UltraSlim 13", 72999),
        ("GameEdge 15 RTX", 98999), ("StudentBook 11", 34999),
    ],
    "laptop_accessories": [
        ("Wireless Mouse", 799), ("USB-C Hub 7-in-1", 1899), ("Laptop Backpack", 1499),
        ("Cooling Pad", 1199), ("Bluetooth Keyboard", 1699),
    ],
    "audio": [
        ("Wireless Earbuds Pro", 3499), ("Over-Ear Headphones", 4999),
        ("Portable Bluetooth Speaker", 2299), ("Neckband Earphones", 999),
    ],
    "mobile_accessories": [
        ("Fast Charger 65W", 1299), ("Power Bank 20000mAh", 1799),
        ("Phone Case (assorted)", 499), ("Tempered Glass Pack", 299),
    ],
    "smart_home": [
        ("Smart Plug", 899), ("Smart Bulb (color)", 749), ("Video Doorbell", 5499),
        ("Smart Speaker Mini", 2999),
    ],
    "wearables": [
        ("Fitness Band", 1999), ("Smartwatch Lite", 4499), ("Smartwatch Pro", 8999),
    ],
    "gaming": [
        ("Gaming Mouse RGB", 1599), ("Mechanical Keyboard", 3999),
        ("Gaming Headset", 2499), ("Controller (wireless)", 2999),
    ],
}

FIRST_NAMES = ["Aarav", "Vivaan", "Aditi", "Ananya", "Rohan", "Ishaan", "Diya",
               "Kabir", "Meera", "Aryan", "Sara", "Vihaan", "Priya", "Arjun", "Neha"]
LAST_NAMES = ["Sharma", "Verma", "Iyer", "Reddy", "Nair", "Gupta", "Khan", "Rao", "Mehta", "Das"]


def seed():
    # Schema is owned by Alembic migrations now, not this script. Fail with
    # a clear instruction rather than silently creating tables out-of-band,
    # which would drift from migrations/versions/ over time.
    inspector = inspect(engine)
    if "merchants" not in inspector.get_table_names():
        raise RuntimeError(
            "Database has no tables yet. Run migrations first:\n"
            "    PYTHONPATH=. alembic upgrade head\n"
            "then re-run this seed script."
        )
    db = SessionLocal()
    try:
        if db.query(Merchant).first():
            print("Seed data already exists — skipping. Delete dev.db to reseed.")
            return

        merchant = Merchant(name="UrbanTech Electronics", business_category="consumer_electronics", currency="INR")
        db.add(merchant)
        db.flush()

        # Default policy limits — deterministic, editable later via the
        # dashboard's Policies section. The LLM never sets these.
        default_policies = [
            ("max_discount_percent", "15", "Maximum discount % the agent may propose without escalation"),
            ("max_campaign_budget", "50000", "Maximum campaign budget (INR) requiring only standard approval"),
            ("max_autonomous_transaction_amount", "0", "Amount above which any transaction needs human approval (0 = always require approval)"),
            ("max_refund_amount_autonomous", "0", "Refunds are never auto-approved in this demo"),
        ]
        for key, value, desc in default_policies:
            db.add(Policy(merchant_id=merchant.id, key=key, value=value, description=desc))

        products: list[Product] = []
        for category, items in CATEGORIES.items():
            for i, (name, price) in enumerate(items):
                p = Product(
                    merchant_id=merchant.id,
                    sku=f"{category[:3].upper()}-{i+1:03d}",
                    name=name,
                    description=f"{name} — popular pick in {category.replace('_', ' ')}.",
                    category=category,
                    price=price,
                    currency="INR",
                    inventory_count=random.randint(15, 200),
                    is_active=True,
                )
                products.append(p)
        db.add_all(products)
        db.flush()

        by_category = {}
        for p in products:
            by_category.setdefault(p.category, []).append(p)

        customers = []
        for i in range(40):
            fn, ln = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
            c = Customer(merchant_id=merchant.id, name=f"{fn} {ln}", email=f"{fn.lower()}.{ln.lower()}{i}@example.com")
            customers.append(c)
        db.add_all(customers)
        db.flush()

        # --- Order history: last 270 days, with intentional patterns ---
        #
        # The analytics layer (analytics_service.py) compares the most recent
        # 30-day window against the 30 days before it. At the order volume a
        # 270-day demo history can realistically hold (~2-4 orders/day), a
        # *smooth* probability curve is too noisy to reliably show up in that
        # specific 60-day comparison — it can (and did, on a first pass)
        # randomly invert. So instead of a gentle multiplier that trends over
        # the whole 270 days, the signals below are applied as a sharp,
        # deliberate step change specifically across the current-vs-previous
        # 30-day boundary the analytics functions actually compare. This
        # makes the demo narrative a designed, reproducible property of the
        # data rather than a coincidence of the random seed.
        now_ref = datetime.utcnow()
        start = now_ref - timedelta(days=270)
        laptops = by_category["laptops"]
        laptop_accessories = by_category["laptop_accessories"]

        for day in range(270):
            order_date = start + timedelta(days=day)
            days_ago = (now_ref - order_date).days  # 0 = today, 269 = oldest

            # Pattern 1: smart_home sharply up in the current 30-day window,
            # roughly flat before that — "which products should I promote".
            if days_ago <= 30:
                smart_home_boost = 4.0
            else:
                smart_home_boost = 1.0

            # Pattern 2: wearables sharply down in the current 30-day window
            # — the factual basis for a "revenue drop" investigation.
            wearables_penalty = 0.15 if days_ago <= 30 else 1.0

            # Pattern 3: payment failure rate elevated in the current 30-day
            # window — gives "why did revenue drop" a second, corroborating
            # cause (not just fewer orders, but more failed ones) instead of
            # a single coincidental signal.
            failure_rate = 0.16 if days_ago <= 30 else 0.05

            # More orders/day than a smooth curve needs, specifically so the
            # current-vs-previous 30-day comparison has enough samples per
            # category that the step changes above are visible rather than
            # drowned in per-day noise.
            base_orders_today = random.randint(3, 6)
            for _ in range(base_orders_today):
                customer = random.choice(customers)
                num_items = random.choices([1, 2, 3], weights=[0.55, 0.32, 0.13])[0]
                chosen: list[Product] = []

                # Pattern 4: laptop buyers frequently also buy an accessory —
                # real signal for the cross-sell/bundle scenario. Kept
                # independent of the time-windowed patterns above/below.
                if random.random() < 0.4:
                    laptop = random.choice(laptops)
                    chosen.append(laptop)
                    if random.random() < 0.55:
                        chosen.append(random.choice(laptop_accessories))
                else:
                    weighted_categories = []
                    for cat in by_category:
                        weight = 1.0
                        if cat == "smart_home":
                            weight *= smart_home_boost
                        if cat == "wearables":
                            weight *= wearables_penalty
                        weighted_categories.extend([cat] * max(1, int(weight * 3)))
                    for _ in range(num_items):
                        cat = random.choice(weighted_categories)
                        chosen.append(random.choice(by_category[cat]))

                if not chosen:
                    continue

                total = sum(float(p.price) for p in chosen)
                # Occasional payment failures — gives Scenario 6 (payment
                # failure handling) real data to point at, not a staged demo.
                status = "paid" if random.random() > failure_rate else "failed"

                order = Order(
                    merchant_id=merchant.id,
                    customer_id=customer.id,
                    status=status,
                    total_amount=total,
                    currency="INR",
                    source="dashboard",
                    created_at=order_date,
                )
                db.add(order)
                db.flush()

                for p in chosen:
                    db.add(OrderItem(order_id=order.id, product_id=p.id, quantity=1, unit_price=p.price))

        db.commit()
        print(f"Seeded merchant '{merchant.name}' (id={merchant.id})")
        print(f"  Products:  {len(products)}")
        print(f"  Customers: {len(customers)}")
        print(f"  Orders:    {db.query(Order).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()