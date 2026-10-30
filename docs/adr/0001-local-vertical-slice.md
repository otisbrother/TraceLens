# ADR 0001: First executable slice

Accepted 2026-10-29.

Use FastAPI, Pydantic 2, SQLAlchemy 2.0 and Alembic with React/TypeScript/Vite.
PostgreSQL is the Compose database; SQLite is an explicit single-machine fallback
because the current Docker daemon is unavailable. Test SQLite now and do not claim
PostgreSQL runtime verification until it runs. JSON uses a PostgreSQL JSONB variant.

M1 uses a separate durable database-job worker and deterministic facts-based mock.
LangGraph persistence, bounded planning and MCP are M3 work, not claims made about M1.
Keep raw synthetic records in the database, source lookup scoped by case and snapshot,
and recommendations separate from human disposition. No real account actions exist.
Session identity and role come from server-side users, never request role/tenant fields.

No XGBoost, Neo4j, fine-tuning, multi-agent system, cloud deployment or paid calls.
Baseline A and mock reports share the same source access. Benchmark stays not_run.
