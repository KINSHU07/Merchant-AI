-- Runs automatically on first container startup (docker-entrypoint-initdb.d).
-- Enables pgvector so it's available if/when Agentic RAG is actually
-- justified (see docs/AI_ARCHITECTURE_DECISION.md) — not used by default.
CREATE EXTENSION IF NOT EXISTS vector;