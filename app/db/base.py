"""
Database engine/session setup.

Primary path: PostgreSQL, run via `docker compose up -d` from the project
root (see docker-compose.yml). Schema is managed by Alembic migrations
(migrations/versions/), not by this module — run `alembic upgrade head`
before app/db/seed.py.

Falls back to a local SQLite file only if DATABASE_URL is unset, purely so
isolated modules can be smoke-tested without Docker running. Do not rely on
SQLite for real development — JSONB and pgvector, used elsewhere in this
schema, are Postgres-only.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

DATABASE_URL = settings.database_url

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session, closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()