"""
Agent node functions.

Design choice worth calling out: intent CLASSIFICATION is an LLM call
(constrained to a fixed enum via json_mode), but which tools run for a
given intent is a deterministic lookup table, not an LLM decision. This
keeps tool selection testable and auditable — "the agent decided intent=X"
is a single, verifiable LLM output; "and intent=X always calls these
tools" is plain code anyone can read. The LLM only reasons freely over
data it's already been handed, and its second job (the proposed action)
is again constrained to a fixed JSON schema — never free text that could
reach execution.
"""
from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy.orm import Session

from app.agents.state import AgentState
from app.policies import policy_engine
from app.services import analytics_service as analytics
from app.services import catalog_service as catalog
from app.services.llm_service import call_llm_json

ALLOWED_INTENTS = [
    "revenue_analysis",       # "how can I increase revenue", "why did revenue drop"
    "product_promotion",      # "which products should I promote"
    "cross_sell",             # "what should I cross-sell to X buyers", "bundle" ideas
    "low_performance",        # "show me products with low conversion / low sales"
    "create_campaign",        # explicit: "create a 10% offer for X"
    "process_refund",         # "refund this order", "give money back for X"
    "general_question",       # anything else about the business
]

INTENT_SYSTEM_PROMPT = f"""You classify a merchant's request into exactly one intent.

Allowed intents: {ALLOWED_INTENTS}

Respond with ONLY a JSON object: {{"intent": "<one of the allowed intents>"}}
No other text. If nothing fits well, use "general_question"."""


# Fields required per proposed_action.type. Used to validate the LLM's
# output before it ever reaches the policy engine — a malformed or
# incomplete action for its own type is treated as a failed reasoning step,
# never patched with guessed defaults.
_CAMPAIGN_TYPES = {
    "create_discount_campaign",
    "create_bundle_campaign",
    "create_upsell_campaign",
    "create_cross_sell_campaign",
}
_REQUIRED_FIELDS_BY_TYPE: dict[str, set[str]] = {
    "create_discount_campaign": {"name", "target_category", "product_ids", "discount_percent"},
    "create_bundle_campaign": {"name", "target_category", "product_ids", "discount_percent"},
    "create_upsell_campaign": {"name", "target_category", "product_ids"},
    "create_cross_sell_campaign": {"name", "target_category", "product_ids"},
    "create_refund": {"order_id", "amount"},
}


REASONING_SYSTEM_PROMPT = """You are a merchant growth analyst. You are given a merchant's
request and REAL data retrieved from their database (never invent numbers
not present in this data).

Respond with ONLY a JSON object matching this exact shape:
{
  "recommendation": "2-4 sentence explanation grounded in the data provided",
  "evidence": ["short factual bullet citing a specific number from the data", "..."],
  "requires_action": true or false,
  "proposed_action": {
    "type": "create_discount_campaign" | "create_bundle_campaign" | "create_upsell_campaign" | "create_cross_sell_campaign" | "create_refund",
    "name": "short campaign name — required for campaign types, null for create_refund",
    "target_category": "category name or null — only meaningful for campaign types",
    "product_ids": ["id", "..."] or [] — only meaningful for campaign types,
    "discount_percent": number or null — only for create_discount_campaign / create_bundle_campaign,
    "budget": number or null — only meaningful for campaign types,
    "order_id": "exact order id copied from the data provided — required for create_refund, else null",
    "amount": number — must exactly match that order's total_amount from the data — required for create_refund, else null
  } or null,
  "expected_impact": "one sentence, qualitative or with a number from the data, or null"
}

Rules:
- requires_action is true ONLY if the merchant's request is asking you to
  DO something (create a campaign/offer/bundle, or process a refund for a
  specific order), not just asking a question.
- proposed_action must be null if requires_action is false.
- For create_refund: order_id and amount MUST be copied exactly from an
  order actually present in the data you were given. Never invent an
  order_id or amount. If no matching order is present in the data, set
  requires_action to false instead of guessing.
- Never invent a discount_percent or budget the merchant didn't ask for or
  that isn't a reasonable inference from the request; if unsure, propose a
  conservative discount_percent (5-10) rather than guessing high.
- Every claim in "recommendation" and "evidence" must be traceable to a
  number actually present in the data you were given."""


def classify_intent(state: AgentState) -> dict:
    try:
        result = call_llm_json(
            INTENT_SYSTEM_PROMPT,
            f"Merchant request: {state['request_text']}",
            temperature=0.0,
            max_tokens=500,
            reasoning_effort="low",
        )
        intent = result.get("intent")
        if intent not in ALLOWED_INTENTS:
            intent = "general_question"
        return {"intent": intent}
    except Exception as e:
        return {"intent": "unknown", "status": "failed", "error": f"Intent classification failed: {e}"}


def _run_tool(tool_calls: list, retrieved: dict, key: str, tool_name: str, fn, *args, **kwargs) -> None:
    """Runs one tool call, records it (success or failure) — never lets a
    single tool's failure silently corrupt the rest of the run. Positional
    Session args are never logged verbatim (they're internal plumbing, not
    meaningful audit data) — only meaningful positional args are recorded."""
    start = time.monotonic()
    try:
        result = fn(*args, **kwargs)
        retrieved[key] = result
        logged_args = [str(a) for a in args if not isinstance(a, Session)]
        tool_calls.append(
            {
                "tool_name": tool_name,
                "input": kwargs or {"args": logged_args},
                "output_summary": json.dumps(result, default=str)[:500],
                "success": True,
            }
        )
    except Exception as e:
        logged_args = [str(a) for a in args if not isinstance(a, Session)]
        tool_calls.append(
            {
                "tool_name": tool_name,
                "input": kwargs or {"args": logged_args},
                "output_summary": f"ERROR: {e}",
                "success": False,
            }
        )
    finally:
        _ = time.monotonic() - start  # latency captured by caller if needed


def make_run_tools(db: Session):
    """Returns a run_tools node bound to a DB session (tools need a session;
    LangGraph nodes only receive state, so this closes over `db`)."""

    def run_tools(state: AgentState) -> dict:
        merchant_id = state["merchant_id"]
        intent = state["intent"]
        tool_calls: list = []
        retrieved: dict[str, Any] = {}

        if intent == "revenue_analysis":
            _run_tool(tool_calls, retrieved, "revenue_summary", "analytics.revenue_summary",
                       analytics.revenue_summary, db, merchant_id)
            _run_tool(tool_calls, retrieved, "top_products", "analytics.top_products",
                       analytics.top_products, db, merchant_id)
            _run_tool(tool_calls, retrieved, "low_performing_products", "analytics.low_performing_products",
                       analytics.low_performing_products, db, merchant_id)

        elif intent == "product_promotion":
            _run_tool(tool_calls, retrieved, "top_products", "analytics.top_products",
                       analytics.top_products, db, merchant_id)

        elif intent == "cross_sell":
            _run_tool(tool_calls, retrieved, "product_pairs", "analytics.product_pair_frequency",
                       analytics.product_pair_frequency, db, merchant_id)

        elif intent == "low_performance":
            _run_tool(tool_calls, retrieved, "low_performing_products", "analytics.low_performing_products",
                       analytics.low_performing_products, db, merchant_id)

        elif intent == "process_refund":
            # The ONLY data source a refund proposal may be grounded in.
            # The reasoning prompt is explicit: order_id/amount must be
            # copied from here verbatim, never invented.
            _run_tool(tool_calls, retrieved, "recent_orders", "analytics.recent_orders",
                       analytics.recent_orders, db, merchant_id)

        elif intent == "create_campaign":
            _run_tool(tool_calls, retrieved, "product_pairs", "analytics.product_pair_frequency",
                       analytics.product_pair_frequency, db, merchant_id)
            _run_tool(tool_calls, retrieved, "top_products", "analytics.top_products",
                       analytics.top_products, db, merchant_id)
            _run_tool(tool_calls, retrieved, "categories", "catalog.list_categories",
                       catalog.list_categories, db, merchant_id)

            # If the request names a real category, fetch its actual products.
            # Without this, a request like "50% off wearables" gives the LLM
            # no wearables data to ground a proposal in, and it correctly
            # refuses to invent product IDs — but that also means it skips
            # requires_action entirely, so the policy engine never gets a
            # chance to evaluate (and reject) the discount. Grounding the
            # category explicitly lets the LLM propose the real action and
            # lets the policy engine do its job, whichever way it decides.
            categories = retrieved.get("categories", [])
            request_lower = state["request_text"].lower()
            matched_category = next((c for c in categories if c.lower() in request_lower), None)
            if matched_category:
                def _search_and_serialize(category: str):
                    products = catalog.search_products(db, merchant_id, category=category, limit=10)
                    return [
                        {
                            "product_id": p.id,
                            "name": p.name,
                            "category": p.category,
                            "price": float(p.price),
                            "inventory_count": p.inventory_count,
                        }
                        for p in products
                    ]

                _run_tool(
                    tool_calls, retrieved, "category_products", "catalog.search_products",
                    _search_and_serialize, category=matched_category,
                )

        else:  # general_question / unknown — safe default, still grounded in real data
            _run_tool(tool_calls, retrieved, "revenue_summary", "analytics.revenue_summary",
                       analytics.revenue_summary, db, merchant_id)

        return {"tool_calls": tool_calls, "retrieved_data": retrieved}

    return run_tools


def _validate_proposed_action(action: dict, retrieved_data: dict) -> str | None:
    """
    Returns an error string if the action is malformed for its declared
    type, or None if it's acceptable. This is the guard between "the LLM
    said something" and "the policy engine gets to see it" — a
    proposed_action missing fields its own type requires, or a
    create_refund whose order_id/amount don't match a real fetched order,
    must fail the run rather than reach evaluate_action() with holes in it.
    """
    action_type = action.get("type")
    if action_type not in _REQUIRED_FIELDS_BY_TYPE:
        return f"Unknown or missing proposed_action.type: {action_type!r}"

    required = _REQUIRED_FIELDS_BY_TYPE[action_type]
    missing = [f for f in required if action.get(f) in (None, "")]
    if missing:
        return f"proposed_action of type {action_type!r} is missing required field(s): {missing}"

    if action_type == "create_refund":
        recent_orders = retrieved_data.get("recent_orders") or []
        order_ids = {o["order_id"] for o in recent_orders}
        if action.get("order_id") not in order_ids:
            return (
                f"proposed_action.order_id {action.get('order_id')!r} does not match any "
                f"order actually fetched for this run — refusing to trust an invented order_id."
            )
        matching = next(o for o in recent_orders if o["order_id"] == action["order_id"])
        if abs(float(action.get("amount", 0)) - matching["total_amount"]) > 0.01:
            return (
                f"proposed_action.amount {action.get('amount')} does not match order "
                f"{action['order_id']}'s actual total_amount {matching['total_amount']}."
            )

    return None


def reason(state: AgentState) -> dict:
    payload = {
        "merchant_request": state["request_text"],
        "intent": state["intent"],
        "data": state.get("retrieved_data", {}),
    }
    try:
        result = call_llm_json(
            REASONING_SYSTEM_PROMPT,
            json.dumps(payload, default=str),
            temperature=0.3,
            max_tokens=2500,
            reasoning_effort="low",
        )
    except Exception as e:
        return {"status": "failed", "error": f"Reasoning step failed: {e}"}

    requires_action = bool(result.get("requires_action", False))
    proposed_action = result.get("proposed_action") if requires_action else None

    if proposed_action:
        validation_error = _validate_proposed_action(proposed_action, state.get("retrieved_data", {}))
        if validation_error:
            return {
                "status": "failed",
                "error": f"Reasoning step produced an invalid proposed_action: {validation_error}",
                "recommendation": result.get("recommendation", ""),
                "evidence": result.get("evidence", []),
            }

    return {
        "recommendation": result.get("recommendation", ""),
        "evidence": result.get("evidence", []),
        "requires_action": requires_action,
        "proposed_action": proposed_action,
        "expected_impact": result.get("expected_impact"),
        # If no action is needed, the run is done — no policy step applies.
        "status": "completed" if not requires_action else "pending_policy",
    }


def make_check_policy(db: Session):
    def check_policy(state: AgentState) -> dict:
        action = state.get("proposed_action")
        if not action or not action.get("type"):
            return {"status": "failed", "error": "requires_action was true but proposed_action is missing/invalid"}

        try:
            result = policy_engine.evaluate_action(db, state["merchant_id"], action["type"], action)
        except Exception as e:
            return {"status": "failed", "error": f"Policy evaluation failed: {e}"}

        status_map = {
            policy_engine.PolicyDecision.BLOCK: "blocked",
            policy_engine.PolicyDecision.REQUIRES_APPROVAL: "awaiting_approval",
            policy_engine.PolicyDecision.APPROVE: "completed",
        }
        return {
            "policy_decision": result.decision.value,
            "policy_reason": result.reason,
            "policy_id": result.policy_id,
            "status": status_map[result.decision],
        }

    return check_policy