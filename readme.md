# MerchantAI — Agentic Merchant Growth & Commerce Platform

**Track 01 — AI Growth & Agentic Commerce**
*"Grow the merchant's revenue, and make them sellable to AI buyers."*

## 1. Problem

Merchants sit on data — orders, products, customer behavior — that could
drive real revenue decisions, but turning that data into action (a
campaign, a discount, a bundle) normally requires a human to notice the
opportunity, decide on parameters, and execute it. AI agents can close
that loop, but an agent that can autonomously create discounts or move
money is a genuine risk if nothing constrains it.

## 2. Solution

MerchantAI is an AI agent a merchant talks to in natural language
("How can I increase revenue this week?", "Create a 10% offer for laptop
buyers"). The agent:

1. Classifies intent and pulls **real** data from Postgres (never invents
   numbers).
2. Reasons over that data with an LLM to produce a recommendation and,
   where relevant, a **structured** proposed action.
3. Runs the proposed action through a **deterministic policy engine**
   (discount limits, budget caps) that can outright reject it.
4. If not rejected, the action still requires **explicit human approval**
   before anything is created.
5. Every step — every tool call, every LLM output, every policy decision,
   every approval — is written to an **audit log**.

It also exposes an AI-readable product catalog so an external AI buyer
agent can discover and (in test mode) purchase products.

## 3. Why this matters — the judging bar

> "Every money action must be explainable, bounded and gated. Show the
> audit trail and one failure handled gracefully."

This is the design constraint the whole system is built around, not a
checklist item added at the end:

```
LLM proposes.
Data systems provide facts.
Deterministic policies authorize.
Humans approve consequential actions.
Execution is honest about what did or didn't happen.
Audit logs record everything.
```

Concretely: the LLM **cannot** call Razorpay, **cannot** create a campaign,
and **cannot** decide a discount is acceptable. It can only produce a
structured JSON proposal. Everything after that is deterministic code.

## 4. Architecture

```
                    Merchant (natural language)
                              │
                              ▼
                    ┌──────────────────┐
                    │  LangGraph Agent │
                    └────────┬─────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        Analytics Tool   Catalog Tool     (Agentic RAG —
        (pure SQL)       (pure SQL)        not used, see §12)
              │               │
              └───────┬───────┘
                       ▼
                LLM Reasoning (Groq, open-source model)
                       │
                       ▼
              Structured Proposed Action (JSON)
                       │
                       ▼
              ┌─────────────────┐
              │  Policy Engine  │  ◄── deterministic, no LLM
              └────────┬────────┘
                        │
            ┌───────────┴───────────┐
            ▼                       ▼
         BLOCK                 REQUIRES_APPROVAL
      (never executes)              │
                                     ▼
                            Human Approval (dashboard)
                                     │
                          ┌──────────┴──────────┐
                          ▼                      ▼
                      APPROVED               REJECTED
                          │
                          ▼
                    Action Executor
                (Campaign: implemented
                 Razorpay: NOT YET — see §10)
                          │
                          ▼
                     Audit Log
```

## 5. Agent workflow (LangGraph)

`classify_intent → run_tools → reason → [check_policy] → end`

- **classify_intent**: one constrained LLM call, output is one of a fixed
  set of intents (`revenue_analysis`, `create_campaign`, etc.) — never
  free text.
- **run_tools**: which tools run is a deterministic lookup table keyed by
  intent, not an LLM decision. If a campaign request names a real
  category, its actual products are fetched so the LLM has real data to
  ground a proposal in (rather than refusing to propose anything or,
  worse, inventing product IDs).
- **reason**: one constrained LLM call over the retrieved data, output is
  a fixed JSON schema (`recommendation`, `evidence`, `requires_action`,
  `proposed_action`, `expected_impact`).
- **check_policy**: only runs if `requires_action` is true. Never an LLM
  call — see §7.

Every node fails closed: malformed LLM output, an exception, or an
unrecognized action type all stop the run rather than proceeding on bad
data.

## 6. Tool architecture

- `analytics_service` — revenue summary, top/low-performing products,
  cross-sell pair frequency, category trend. Pure SQL aggregation.
- `catalog_service` — product search, single-product lookup, stock
  checks, and an AI-readable catalog format for external AI buyers. Price
  and inventory always come from here, never from LLM memory.
- Both are plain Python functions with typed signatures — no arbitrary
  SQL, no arbitrary HTTP calls are ever exposed to the LLM.

## 7. Policy engine

Deterministic, no LLM involvement, fail-safe defaults (an unrecognized
action type is **blocked**, not guessed at). Reads merchant-configurable
limits from the `policies` table:

| Key | Default | Effect |
|---|---|---|
| `max_discount_percent` | 15 | Discount above this → BLOCK |
| `max_campaign_budget` | 50000 | Budget above this → BLOCK |
| `max_autonomous_transaction_amount` | 0 | Any transaction → REQUIRES_APPROVAL |
| `max_refund_amount_autonomous` | 0 | Any refund → REQUIRES_APPROVAL |

Campaigns within limits still require human approval — there is no fully
autonomous financial action in this system by design.

## 8. Human approval

Every action that reaches `REQUIRES_APPROVAL` becomes an `Approval` row
(`pending`). Approving or rejecting it is a one-time, irreversible
decision — a second approve/reject call on an already-decided approval is
rejected outright (no replay, no double-execution).

## 9. Audit trail

Every run writes an `AgentRun` row, one `ToolCall` row per tool
invocation (success or failure), and `AuditLog` entries at each
transition: `request_received → tool_invoked → recommendation_made →
policy_checked → approval_requested/action_blocked → approval_decided →
action_executed/action_failed`. A run's full decision chain is
reconstructable from the database alone.

## 10. Razorpay integration — status

**Not yet implemented.** The policy engine and approval workflow already
handle `create_order`/`create_transaction`/`create_refund` action types
correctly — they get gated and approved exactly like campaigns — but the
executor has no Razorpay client to actually call. Approving one of these
actions logs an honest `action_failed` / `NOT EXECUTED` and the run status
becomes `approved_not_executed`. This is deliberate: a fake success
message would be worse than an honest gap. See `app/agents/executor.py`.

## 11. AI buyer flow

`GET /api/buyer/catalog/{merchant_id}` returns a machine-readable product
list (id, name, price, currency, availability, inventory). An external AI
buyer can discover products this way; `POST /api/buyer/orders/{merchant_id}`
accepts a purchase intent. The LLM never determines the final payable
amount — it's always computed from the authoritative product price.

## 12. RAG decision

**Not used**, deliberately. Every question this system answers — revenue,
product performance, cross-sell patterns, inventory, pricing — is
structured, relational data that SQL answers exactly and cheaply.
Introducing a vector database and retrieval pipeline would add latency,
cost, and a new failure mode (retrieval mismatch, prompt injection via
retrieved content) for no accuracy benefit here. If a future version adds
unstructured merchant knowledge (return policies, FAQs, marketing copy),
Agentic RAG becomes justified and should be added as a tool the agent
decides to invoke — not a default pipeline every query passes through.

## 13. Tech stack

- **Backend**: FastAPI, PostgreSQL (+ pgvector, unused but available),
  SQLAlchemy, Alembic
- **Agent**: LangGraph
- **LLM**: Groq API — open-source models (`openai/gpt-oss-120b`), hosted,
  no local model weights
- **Frontend**: Streamlit
- **Payments**: Razorpay (test mode only; not yet wired to an executor)

## 14. Quick start

See `HANDS_ON.md` for full setup, run, and troubleshooting instructions.

## 15. Known limitations

- Razorpay execution not implemented (approvals for payment actions are
  honestly marked not-executed, never faked)
- Single-merchant demo; no authentication/multi-tenant login layer yet
- No automated CI/evaluation suite yet — verification so far has been
  targeted manual/scripted tests per component, documented inline in the
  relevant source files