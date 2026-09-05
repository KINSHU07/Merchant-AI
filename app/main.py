"""FastAPI app — pure wiring. No business logic lives here."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import agent, approvals, audit, buyer, campaigns, policies

app = FastAPI(title="Merchant AI", version="0.1.0")

# Local dev only: allows the browser-based dashboard (served from a
# different origin than localhost:8000) to call this API directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (agent.router, approvals.router, audit.router, campaigns.router, policies.router, buyer.router):
    app.include_router(router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}