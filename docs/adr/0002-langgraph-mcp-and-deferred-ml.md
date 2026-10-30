# ADR 0002: LangGraph single-agent orchestration, stdio MCP, and deferred ML

Accepted 2026-10-29.

## Context

Following M1's executable vertical slice, M2–M4 require structured multi-step investigation
(hypotheses, bounded next-step tool selection, counterevidence search, versioned policy RAG,
human-in-the-loop review with pause/resume, and local MCP interoperability) plus an A/B/C
evaluation harness.

## Decisions

1. **Single Bounded LangGraph State Machine (`apps/worker/workflow.py`):**
   - Use a single deterministic state machine with explicit node transitions (`load_and_verify_scope`
     -> `gather_core_facts` -> `formulate_hypotheses` -> `select_next_step` <-> `execute_targeted_tool`
     -> `lookup_policy` -> `draft_validate_and_publish` -> `human_review_interrupt` -> `finalize_disposition`).
   - Do not use unconstrained multi-agent conversation loops; one bounded agent with a strict
     6-tool / 3-LLM-call budget is easier to audit, debug, and bound in latency and cost.
2. **Persistent Checkpointer with Released Worker Lease Before Interrupt:**
   - Persist graph state via `SqliteSaver` (`<db>.checkpoints.sqlite`) on local SQLite or
     the same SQLite saver on a shared volume in Compose, keyed by `thread_id = run.id`.
     The PostgreSQL checkpointer dependency is installed but not wired or verified.
   - Release the database job lease (`Job.status = "done"`) inside `draft_validate_and_publish`
     immediately before `interrupt()`, ensuring a case awaiting human review never holds a database
     transaction or blocks worker capacity.
3. **Shared Read-Only Domain Services for Direct Tools and Stdio MCP (`apps/api/tools.py`, `apps/mcp/`):**
   - Both the direct adapter and the official `mcp` SDK stdio server invoke the exact same read-only
     domain functions (`apps/api/domain.py`).
   - Trusted scope (`actor_id`, `tenant_id`, `role`, `case_id`, `snapshot_id`, `cutoff`) is signed
     server-side via HMAC-SHA256 (`scope_token`) and re-verified against the database inside a
     read-only transaction (`PRAGMA query_only=ON` / `SET TRANSACTION READ ONLY`).
4. **Explicit Provider & Retrieval Separation (`LLM_MODE=mock|live`, `RETRIEVAL_MODE=lexical|hybrid`):**
   - Offline `mock` + `lexical` (BM25 + term overlap) runs deterministically without external keys.
   - `live` LLM and `hybrid` vector retrieval require explicit server-side credentials and fail
     closed (503 / `LIVE_PROVIDER_NOT_CONFIGURED`) if missing, never silently substituting mock output
     or random vectors.
5. **Why XGBoost, GNN, Neo4j, Leiden, Multi-Agent, and Fine-Tuning/DPO are Deferred:**
   - Upstream alert generation is not the bottleneck being studied; our value hypothesis tests
     evidence-driven investigation and counterevidence verification on top of existing alerts.
   - Bounded 2-hop subgraphs (`<= 50` nodes, `<= 100` edges) are computed cleanly in-memory from
     scoped relational queries without operational overhead of a separate Neo4j cluster.
   - Fine-tuning/DPO is premature before collecting verified human review dispositions in production.

