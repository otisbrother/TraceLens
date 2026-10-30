# Pre-publication review — 2026-10-30

Scope: backend, workflow, data, evaluation, frontend, tests and packaging for GitHub publication.
No cloud deployment or real-provider requests were performed.

## Corrected findings

1. **Evidence bypass:** workflow discarded tool outputs and did a direct final `gather_facts`, bypassing
   selected tools, transport, STOP and budgets. Reports now aggregate recorded observations; independent
   fact recomputation is validation only. Uncollected facts remain absent; incomplete runs abstain.
2. **Parent scope / idempotency:** parent IDs could reference a foreign case; reused request keys could
   change workflow mode silently. Validate parent tenant/case and request identity before queuing.
3. **Review resume loss:** checkpoint exceptions were swallowed after marking completed. Persist
   `review_pending_resume` and a clean audit event; worker and duplicate review can retry the durable decision.
4. **Provider bounds:** retries were not counted as calls; malformed outer JSON was an untyped error.
   Count attempts, cap requested output, reserve report call budget and validate response/summary types.
5. **Benchmark provenance:** CLI mode could disagree with actual configured provider; live limit was not
   enforced; test runs rewrote tracked gold and deleted fixed cache paths. Preflight now blocks mismatches,
   isolates each experiment and saves actual per-case reports/evidence/errors. Interpretations are qualified.
6. **Packaging:** add `.dockerignore` to exclude secrets, local databases, gold and host dependencies.
   Normal seed no longer creates a gold directory inside runtime. Include safe summary artifacts in image.
7. **UI/test claims:** remove fixed “100%” citation success and inaccurate graph-step count. Replace the
   shallow browser test with worker/report/source/reviewer/audit/download acceptance and mobile isolation.
8. **CLI queue:** stop forcibly reopening an already-investigating case; preserve the active job.

## Remaining limitations

- Docker/PostgreSQL build/run not verified without daemon; checkpoint implementation is SQLite only.
- Live planner/summary and hybrid adapters have no real-provider smoke result; report claims/recommendation
  remain deterministic. Full MVP is not declared done.
- Tool SQL loads are scoped but not universally query-bounded; declared timeouts are not hard execution isolation.
- Source snapshot IDs are not immutable historical storage. SQL-level audit protection/RLS is not configured.
- Policy lookup uses case cutoff; transaction-time versus present investigation SOP is not fully separated.
- Worker retry can restart collection; concurrent writers around a checkpoint and all crash windows need further tests.
- Evaluator is rule/template aligned; its metrics cannot prove adaptive reasoning superiority or business value.

Actual final check results and next work are maintained in `IMPLEMENTATION_STATUS.md`.
