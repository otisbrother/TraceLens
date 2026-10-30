# Evaluation: what is actually measured

All data is synthetic. Mock results test deterministic plumbing and templates, not LLM intelligence,
real-world fraud accuracy, human completion time or financial return. Live model and human study: `not_run`.

## Modes

- **A:** all authorized dashboard facts + an unfilled deterministic report template; no LLM.
  Its low recommendation score is not evidence that a human investigator performs poorly.
- **B:** fixed context → ledger → trips → policies checklist. Template recommendations; live summary optional.
- **C:** context → ledger, then bounded planner. Mock inspects device intervals and trips, requests graph
  when overlap is present, then policies. Reports use actual returned observations; missing tools are not
  silently supplied by a hidden final gather. Live planner and summary are adapters, not full LLM report synthesis.

B and C deliberately use different templates/checklists. These are not controlled LLM ablations.

## Data and gold

Generator supports demo (12 tenant A + 1 tenant B), dev (50, seed 101), heldout (150, seed 202).
IDs are disjoint. Scenario families and rule-derived outcomes are shared, so heldout is **not** an
independent reasoning/generalization test. Scores can be perfect because generation and templates align.

Only evaluator code reads gold. Each experiment writes gold and fixtures under its own
`.cache/benchmarks/<uuid>/` directory; ordinary runtime seed does not write gold.
Docker excludes the repository gold directory. Runtime mount isolation still needs verification on a running daemon.

## Metrics and limitations

- Evidence/counterevidence recall: matched named rubric signals / required gold signals. Matching currently
  uses claim IDs/text and facts, so this is a heuristic coverage measure, not independent semantic verification.
- Citation validity: existing in-scope evidence with nonempty accessible sources, plus valid policy refs / all cited refs.
- Unsupported claims: a limited set of template conditions, not a complete factuality judge.
- Recommendation agreement: template recommendation / synthetic rubric, with confusion matrix.
- Quality denominators are successful reports; failures also appear as per-case outputs and `failure_count`.
  `attempted_cases` includes failures. Latency includes failed attempts; inspect sample sizes.
- Bootstrap is only produced at >=20 successful cases. A degenerate [1,1] interval on rule-aligned synthetic
  samples does not establish certainty or real-world validity.

Artifacts record split/seed/hash, budgets, mode/model, outputs/evidence/errors and timestamps.
The dataset hash currently hashes gold metadata, not a cryptographic commitment to all operational rows.
Retained experiment databases enable inspection. Prices and token provenance remain separate from quality.

## Run

```powershell
uv run --frozen python -m evaluation.runners.run_benchmark --split dev --seed 101 --limit 6
uv run --frozen python -m evaluation.runners.run_benchmark --split heldout --seed 202 --limit 6 --save-default-summary
```

CLI provider mode must match `LLM_MODE`. Paid batches require explicit `--allow-paid-live --limit N`, valid
credentials and no degraded fallback. Offline mock requires lexical retrieval to avoid embedding charges.
Historical pre-review artifacts must not be presented as results for the reviewed implementation.

## Human study, not yet run

Use consented participants, counterbalanced assignments and the same source access for A/B/C.
Predefine evidence-quality rubric, measure actual completion time, corrections and final decisions;
blind the scoring where practical and report failures/abstentions. Do not generate artificial human timings.
