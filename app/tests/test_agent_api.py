"""tests/test_agent_api.py — same approach as your CLI smoke test: real
DB, real policy engine, real executor; only call_llm_json is mocked, since
that's the one non-deterministic boundary."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.base import Base, SessionLocal, engine
from app.main import app
from app.models.commerce import Merchant
from app.models.governance import Policy


@pytest.fixture()
def client(monkeypatch):
    Base.metadata.create_all(bind=engine)  # SQLite in-memory/dev.db per your config
    db = SessionLocal()
    merchant = Merchant(name="Test Co")
    db.add(merchant)
    db.add(Policy(merchant_id=merchant.id, key="max_discount_percent", value="15"))
    db.commit()
    merchant_id = merchant.id
    db.close()

    call_count = {"n": 0}

    def fake_call_llm_json(system_prompt, user_prompt, **kwargs):
        call_count["n"] += 1
        if "classify" in system_prompt.lower():
            return {"intent": "create_campaign"}
        return {
            "recommendation": "Laptop buyers respond well to a modest bundle discount.",
            "evidence": ["Laptop + accessory co-purchase observed in the data."],
            "requires_action": True,
            "proposed_action": {
                "type": "create_discount_campaign",
                "name": "Laptop Buyer Discount",
                "target_category": "laptops",
                "product_ids": [],
                "discount_percent": 10,
                "budget": None,
            },
            "expected_impact": "Modest uplift in accessory attach rate.",
        }

    monkeypatch.setattr("app.services.llm_service.call_llm_json", fake_call_llm_json)

    with TestClient(app) as c:
        c.merchant_id = merchant_id  # stash for tests
        yield c


def test_full_approval_loop(client):
    # 1. Trigger a run
    resp = client.post(
        "/api/agent/runs",
        json={"merchant_id": client.merchant_id, "request_text": "Create a 10% offer for laptop buyers."},
    )
    assert resp.status_code == 201
    run = resp.json()
    assert run["status"] == "awaiting_approval"

    # 2. Find the pending approval
    resp = client.get("/api/approvals", params={"merchant_id": client.merchant_id})
    approvals = resp.json()
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval["status"] == "pending"
    assert approval["action_type"] == "create_discount_campaign"

    # 3. Approve it
    resp = client.post(f"/api/approvals/{approval['id']}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"

    # 4. Re-approving must fail (replay protection)
    resp = client.post(f"/api/approvals/{approval['id']}/approve")
    assert resp.status_code == 409

    # 5. Campaign should now exist and be active
    resp = client.get("/api/campaigns", params={"merchant_id": client.merchant_id})
    campaigns = resp.json()
    assert len(campaigns) == 1
    assert campaigns[0]["status"] == "active"