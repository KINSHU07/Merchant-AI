"""
Merchant AI — governance dashboard (Streamlit).

Talks directly to the FastAPI backend over plain HTTP — no CORS concerns
since this runs as its own Python process, not in a browser sandbox.

Run with:
    pip install streamlit requests
    streamlit run dashboard.py
"""
from __future__ import annotations

import requests
import streamlit as st

st.set_page_config(page_title="Merchant AI — Governance Console", layout="wide")

# ---------- Sidebar: connection config ----------

st.sidebar.title("Ledger")
st.sidebar.caption("Agent governance console")

base_url = st.sidebar.text_input("API base URL", value="http://127.0.0.1:8000")
merchant_id = st.sidebar.text_input("Merchant ID", value="")

if st.sidebar.button("Refresh", use_container_width=True):
    st.rerun()


def api_get(path: str, params: dict | None = None):
    try:
        resp = requests.get(f"{base_url}{path}", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json(), None
    except requests.exceptions.RequestException as e:
        detail = None
        try:
            detail = e.response.json().get("detail") if e.response is not None else None
        except Exception:
            pass
        return None, detail or str(e)


def api_post(path: str, json: dict | None = None):
    try:
        resp = requests.post(f"{base_url}{path}", json=json, timeout=30)
        resp.raise_for_status()
        return resp.json(), None
    except requests.exceptions.RequestException as e:
        detail = None
        try:
            detail = e.response.json().get("detail") if e.response is not None else None
        except Exception:
            pass
        return None, detail or str(e)


STATUS_COLOR = {
    "approved": "green", "active": "green", "completed": "green", "action_executed": "green",
    "pending": "orange", "awaiting_approval": "orange", "requires_approval": "orange",
    "rejected": "red", "blocked": "red", "failed": "red", "action_failed": "red",
    "approved_not_executed": "red",
}


def pill(status: str) -> str:
    color = STATUS_COLOR.get((status or "").lower(), "gray")
    return f":{color}[{status}]"


if not merchant_id:
    st.info("Enter a merchant ID in the sidebar to load data.")
    st.stop()

tab_overview, tab_trigger, tab_approvals, tab_campaigns, tab_buyer, tab_audit = st.tabs(
    ["Overview", "Trigger agent", "Approvals", "Campaigns", "Buyer catalog", "Audit log"]
)

# ---------- Overview ----------

with tab_overview:
    st.header("Overview")

    runs, err1 = api_get("/api/agent/runs", {"merchant_id": merchant_id, "limit": 10})
    approvals, err2 = api_get("/api/approvals", {"merchant_id": merchant_id, "status": "pending", "limit": 20})
    campaigns, err3 = api_get("/api/campaigns", {"merchant_id": merchant_id, "limit": 20})

    for err in (err1, err2, err3):
        if err:
            st.error(err)

    if runs is not None and approvals is not None and campaigns is not None:
        c1, c2, c3 = st.columns(3)
        c1.metric("Pending approvals", len(approvals))
        c2.metric("Active campaigns", sum(1 for c in campaigns if c["status"] == "active"))
        c3.metric("Recent runs", len(runs))

        st.subheader("Recent agent runs")
        if not runs:
            st.caption("No runs yet. Trigger one from the 'Trigger agent' tab.")
        for r in runs:
            with st.container(border=True):
                col_a, col_b = st.columns([5, 1])
                col_a.markdown(f"**{r['request_text']}**")
                col_a.caption(f"{r.get('intent') or '—'} · {r['created_at']}")
                col_b.markdown(pill(r["status"]))

# ---------- Trigger agent ----------

with tab_trigger:
    st.header("Trigger agent")
    st.caption(
        "Describe what you want the merchant's agent to consider. It reasons over real store "
        "data and, if it proposes an action, that action still needs your approval."
    )

    request_text = st.text_area(
        "Request", placeholder="e.g. Create a 10% offer for laptop buyers.", height=100
    )

    if st.button("Run agent", type="primary", disabled=not request_text.strip()):
        with st.spinner("Running agent…"):
            result, err = api_post("/api/agent/runs", {"merchant_id": merchant_id, "request_text": request_text})
        if err:
            st.error(err)
        else:
            st.session_state["last_run"] = result

    if "last_run" in st.session_state:
        run = st.session_state["last_run"]
        with st.container(border=True):
            col_a, col_b = st.columns([5, 1])
            col_a.markdown("**Result**")
            col_b.markdown(pill(run["status"]))
            if run.get("recommendation"):
                st.write(run["recommendation"])
            if run.get("proposed_action"):
                st.markdown("**Proposed action**")
                st.json(run["proposed_action"])
            if run["status"] == "awaiting_approval":
                st.caption("→ Review this in the Approvals tab.")

# ---------- Approvals ----------

with tab_approvals:
    st.header("Pending approvals")

    approvals, err = api_get("/api/approvals", {"merchant_id": merchant_id, "status": "pending", "limit": 20})
    if err:
        st.error(err)
    elif not approvals:
        st.caption("Nothing pending. All clear.")
    else:
        for a in approvals:
            with st.container(border=True):
                col_a, col_b = st.columns([5, 1])
                col_a.markdown(f"**{a['action_type']}**")
                col_a.caption(f"Risk: {a['risk_level']} · {a['created_at']}")
                col_b.markdown(pill(a["status"]))

                st.write(a["reason"])
                with st.expander("Action payload"):
                    st.json(a["action_payload"])

                col_approve, col_reject, _ = st.columns([1, 1, 4])
                if col_approve.button("Approve", key=f"approve_{a['id']}", type="primary"):
                    _, err = api_post(f"/api/approvals/{a['id']}/approve")
                    if err:
                        st.error(err)
                    else:
                        st.rerun()
                if col_reject.button("Reject", key=f"reject_{a['id']}"):
                    _, err = api_post(f"/api/approvals/{a['id']}/reject", {"reason": None})
                    if err:
                        st.error(err)
                    else:
                        st.rerun()

# ---------- Campaigns ----------

with tab_campaigns:
    st.header("Campaigns")

    campaigns, err = api_get("/api/campaigns", {"merchant_id": merchant_id, "limit": 20})
    if err:
        st.error(err)
    elif not campaigns:
        st.caption("No campaigns yet.")
    else:
        for c in campaigns:
            with st.container(border=True):
                col_a, col_b = st.columns([5, 1])
                detail = f"{c['type']} · {c.get('target_category') or 'no category'}"
                if c.get("discount_percent"):
                    detail += f" · {c['discount_percent']}% off"
                col_a.markdown(f"**{c['name']}**")
                col_a.caption(detail)
                col_b.markdown(pill(c["status"]))

# ---------- Buyer catalog ----------

with tab_buyer:
    st.header("Buyer catalog")
    st.caption("What an external AI buyer would see and could purchase from this merchant.")

    category = st.text_input("Filter by category", placeholder="e.g. laptops")
    catalog, err = api_get(
        f"/api/buyer/catalog/{merchant_id}",
        {"limit": 30, **({"category": category} if category else {})},
    )
    if err:
        st.error(err)
    elif not catalog:
        st.caption("No products found.")
    else:
        cols = st.columns(3)
        for i, p in enumerate(catalog):
            with cols[i % 3]:
                with st.container(border=True):
                    st.markdown(f"**{p['name']}**")
                    st.caption(f"{p['category']} · {p['sku']}")
                    st.markdown(f"### {p['price']:,.2f} {p['currency']}")
                    if p["availability"] == "in_stock":
                        st.caption(f":green[{p['inventory_count']} in stock]")
                    else:
                        st.caption(":red[out of stock]")

                    qty = st.number_input(
                        "Qty", min_value=1, value=1, key=f"qty_{p['product_id']}", label_visibility="collapsed"
                    )
                    if st.button(
                        "Buy", key=f"buy_{p['product_id']}",
                        disabled=p["availability"] != "in_stock", use_container_width=True,
                    ):
                        result, err = api_post(
                            f"/api/buyer/orders/{merchant_id}",
                            {"product_id": p["product_id"], "quantity": int(qty)},
                        )
                        if err:
                            st.error(err)
                        else:
                            st.success(f"Order {result['order_id']}: {result['reason']}")

# ---------- Audit log ----------

with tab_audit:
    st.header("Audit log")

    run_filter = st.text_input("Filter by run ID (optional)", placeholder="paste a run id to narrow the trail")
    params = {"merchant_id": merchant_id, "limit": 30}
    if run_filter.strip():
        params["run_id"] = run_filter.strip()

    logs, err = api_get("/api/audit-logs", params)
    if err:
        st.error(err)
    elif not logs:
        st.caption("No audit entries.")
    else:
        for l in logs:
            st.markdown(f"**{l['created_at']}** · `{l['event_type']}`")
            st.write(l["summary"])
            st.divider()