"""app/api/routers/agent.py"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.api.schemas import AgentRunDetailOut, AgentRunOut, AgentRunRequest
from app.db.base import get_db
from app.models.governance import AgentRun

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/runs", response_model=AgentRunOut, status_code=201)
def create_run(payload: AgentRunRequest, db: Session = Depends(get_db)):
    run = run_agent(db, payload.merchant_id, payload.request_text)
    return run


@router.get("/runs", response_model=list[AgentRunOut])
def list_runs(
    merchant_id: str,
    status: str | None = None,
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    q = db.query(AgentRun).filter(AgentRun.merchant_id == merchant_id)
    if status:
        q = q.filter(AgentRun.status == status)
    return q.order_by(AgentRun.created_at.desc()).limit(limit).all()


@router.get("/runs/{run_id}", response_model=AgentRunDetailOut)
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if run is None:
        raise HTTPException(404, f"No agent run found with id {run_id!r}.")
    return run