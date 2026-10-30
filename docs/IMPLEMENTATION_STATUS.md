# Implementation status

Updated: 2026-10-30 (Asia/Ho_Chi_Minh). Local publication review complete.

## Milestones — calibrated against executable evidence

| Milestone | Status | Implemented / outstanding |
| --- | --- | --- |
| M1 full mock slice | done (local) | Migrations, seed, rules, dashboard, report/evidence, review, audit/export. Backend and real-browser acceptance pass. |
| M2 evidence / RAG / live | partial | Scoped tools and lexical retrieval tested. Live adapter implements planner + summary, not full model-authored report; live/hybrid smoke not_run. |
| M3 bounded workflow / MCP | partial | LangGraph SQLite interrupt/resume and stdio MCP implemented. Review recovery added. In-flight job retry restarts collection, not a guaranteed exact-node continuation. PostgreSQL saver not wired. |
| M4 evaluation / delivery | partial | Regenerated dev and heldout mock artifacts, 12 cases per split, with per-case outputs. No independent reasoning benchmark, live run, human study or Docker runtime verification. |

## Checks in this review

- Final `pytest -q`: **28 passed in 41.06s**, including MCP round trip and review recovery.
- Playwright: **2 passed in 19.8s**, using fresh migrations, isolated DB, separate API/worker and Chromium.
  Covers contrasting recommendations, source evidence, reviewer decision, audit/export and mobile tenant isolation.
- `npm.cmd --prefix apps/web run build`: passed (TypeScript + Vite production build).
- `ruff check apps data evaluation migrations tests scripts`: passed.
- `docker compose config --quiet`: passed; Docker daemon unavailable, so image/runtime checks not_run.
- Mock A/B/C: dev seed 101 and heldout seed 202, **12 cases each**, actual outputs saved under `evaluation/results/`.
  These template/rule-aligned scores do not measure live reasoning or compare fairly with human investigators.
- Secret-pattern scan: 84 candidate files, no matching private keys/provider tokens; local env/cache/DB excluded.
- `git diff --check`: passed. Desktop and mobile screenshots inspected; mobile table overflow fixed.

## Review corrections

See [RELEASE_REVIEW.md](RELEASE_REVIEW.md). Critical fixes: no uncounted final data gather,
actual tool observations feed reports; scope-checked parent run; bounded provider attempts;
durable visibility/recovery for failed review resume; benchmark mode checks, isolated gold/output;
Docker ignore rules; end-to-end browser acceptance; removal of hardcoded success claims.

## Verified migration identities

`04c4e95b4d66` -> `8b2e5a91c301`. Earlier documentation named a non-existent initial revision.
Local SQLite + separate SQLite checkpoints; Compose PostgreSQL + shared SQLite checkpoint volume.

## Next work

1. Complete real-provider structured report generation and controlled live smoke with user-supplied key.
2. Verify Compose from clean build and PostgreSQL read-only/session/concurrency behavior.
3. Introduce independent scenario variants/rubric and a human-completed baseline before value claims.
4. Harden source immutability, policy-time semantics, hard tool time/size limits and in-flight checkpoint recovery.

No live credentials, private data, databases or local caches are intended for Git.
