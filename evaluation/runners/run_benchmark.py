"""Benchmark runner comparing Mode A (Baseline), Mode B (Fixed), and Mode C (Evidence-driven).

Safety & Integrity Guarantees:
1. Runtime services (`apps/*`) never read `evaluation/private_gold/`. Gold labels
   stay in the offline runner and are not passed to workflow inputs.
2. Live provider batch runs are blocked by default unless `--allow-paid-live` and
   `--limit` are explicitly passed.
3. Saves full experiment provenance (dataset hash, split, seed, budgets, denominators,
   confusion matrix, latency P50/P95) to `evaluation/results/`.
"""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.config import settings
from apps.api.contracts import Claim, ReportContent, StartInput
from apps.api.db import make_engine
from apps.api.domain import gather_facts, readonly_session, serialize
from apps.api.models import Base, Case, Evidence, InvestigationRun, Report, User, uid, utcnow
from apps.api.services import start_run
from apps.worker.main import process_one
from data.generator.seed import seed_database
from evaluation.rubrics.rubric_v1 import aggregate_mode_metrics, evaluate_case_report

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "evaluation" / "results"


def _build_baseline_mode_a_output(case, envelope) -> tuple[dict, list[dict]]:
    """Mode A: Dashboard + deterministic template without LLM/agent synthesis."""
    ev_id = uid()
    ev_row = {
        "id": ev_id,
        "case_id": case.id,
        "run_id": "baseline-a",
        "tenant_id": case.tenant_id,
        "snapshot_id": case.snapshot_id,
        "kind": "FACT",
        "source_refs": [
            ref for ref in envelope.source_record_ids if ref.startswith(("trip:", "rider:", "usage:"))
        ],
        "observed_at": case.cutoff,
        "retrieved_at": utcnow(),
        "structured_values": envelope.data["facts"],
        "calculation_ref": "baseline-a/facts",
        "completeness": envelope.completeness,
        "freshness": envelope.freshness,
    }
    content = ReportContent(
        summary="Mẫu báo cáo Baseline A (không có trợ lý tổng hợp giả thuyết/phản bác tự động).",
        hypotheses=[],
        supporting_claims=[
            Claim(
                claim_id="shared",
                kind="OBSERVED",
                text="Cảnh báo cơ sở ghi nhận các tài khoản có liên kết thiết bị và áp mã voucher.",
                evidence_ids=[ev_id],
            )
        ],
        counterevidence_claims=[],
        missing_information=[
            Claim(
                claim_id="context",
                kind="OBSERVED",
                text="Điều tra viên cần tự kiểm tra từng bảng nguồn và điền mẫu báo cáo thủ công.",
                evidence_ids=[ev_id],
            )
        ],
        next_steps=["Điều tra viên tự mở bảng chuyến đi, thiết bị, sổ voucher và SOP."],
        recommendation="INSUFFICIENT_EVIDENCE",
        policy_refs=[f"policy:{p['id']}" for p in envelope.data["policies"]],
        facts=envelope.data["facts"],
        validation_findings=[],
        workflow_mode="A",
        provider_mode="none",
        model_id="baseline-dashboard-template-v1",
    )
    return content.model_dump(), [ev_row]


def run_benchmark_suite(
    split: str = "heldout",
    seed: int = 202,
    limit: int | None = None,
    provider_mode: str = "mock",
    allow_paid_live: bool = False,
    output_path: Path | None = None,
) -> dict:
    if provider_mode not in {"mock", "live"}:
        raise ValueError("Invalid provider mode")
    if provider_mode != settings.llm_mode:
        raise ValueError("BENCHMARK_PROVIDER_MODE_MISMATCH: CLI mode must match LLM_MODE")
    if limit is not None and limit <= 0:
        raise ValueError("--limit must be positive")
    if provider_mode == "live" and (not allow_paid_live or limit is None):
        raise ValueError(
            "Batch live benchmark is disabled by default to prevent accidental paid API spend. "
            "Pass --allow-paid-live and --limit <N> explicitly."
        )

    if provider_mode == "mock" and settings.retrieval_mode != "lexical":
        raise ValueError("Offline mock benchmark requires RETRIEVAL_MODE=lexical")
    if provider_mode == "live" and (
        not settings.llm_api_key or not settings.llm_model or settings.llm_allow_degraded_fallback
    ):
        raise ValueError("Live benchmark requires credentials and disables degraded fallback")
    CACHE_DIR = REPO_ROOT / ".cache" / "benchmarks" / str(uuid4())
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    db_file = CACHE_DIR / f"bench_{split}_{seed}.db"

    bench_engine = make_engine(f"sqlite:///{db_file.as_posix()}")
    Base.metadata.create_all(bench_engine)

    with Session(bench_engine) as db:
        seed_database(
            db,
            seed=seed,
            split=split,
            count=limit,
            include_background=False,
            write_gold_files=True,
            gold_output_dir=CACHE_DIR / "private_gold",
            fixtures_output_dir=CACHE_DIR / "fixtures",
        )

    gold_file = CACHE_DIR / "private_gold" / f"{split}_gold.json"
    gold_raw = gold_file.read_text(encoding="utf-8")
    dataset_hash = hashlib.sha256(gold_raw.encode("utf-8")).hexdigest()
    gold_data = json.loads(gold_raw)["cases"]

    with Session(bench_engine) as db:
        all_cases = [
            c.id for c in db.scalars(select(Case).where(Case.tenant_id == "tenant-a").order_by(Case.id)).all()
        ]
    if limit:
        all_cases = all_cases[:limit]

    mode_summaries = []
    outputs = []

    # 1. Evaluate Mode A (Baseline dashboard + deterministic template)
    a_evals, a_latencies, a_tools, a_llms = [], [], [], []
    with readonly_session(bench_engine) as reader:
        actor = reader.get(User, "investigator")
        for cid in all_cases:
            t0 = time.perf_counter()
            case = reader.get(Case, cid)
            env = gather_facts(reader, actor, cid)
            rep_payload, ev_rows = _build_baseline_mode_a_output(case, env)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            a_latencies.append(elapsed_ms)
            a_tools.append(1)
            a_llms.append(0)
            outputs.append(
                {
                    "case_id": cid,
                    "mode": "A",
                    "status": "completed",
                    "report": rep_payload,
                    "evidence": ev_rows,
                    "latency_ms": elapsed_ms,
                }
            )
            a_evals.append(evaluate_case_report(reader, actor, case, rep_payload, ev_rows, gold_data[cid]))
    mode_summaries.append(aggregate_mode_metrics("A", a_evals, a_latencies, a_tools, a_llms, seed=seed))

    # 2. Evaluate Mode B (Fixed tool checklist) and Mode C (Evidence-driven adaptive workflow)
    for wf_mode in ["B", "C"]:
        wf_evals, wf_latencies, wf_tools, wf_llms = [], [], [], []
        failures, timeouts = 0, 0
        for cid in all_cases:
            t0 = time.perf_counter()
            with Session(bench_engine) as db:
                actor = db.get(User, "investigator")
                case = db.get(Case, cid)
                case.status = "open"
                db.commit()
                run_info = start_run(
                    db,
                    actor,
                    cid,
                    StartInput(
                        idempotency_key=f"bench-{wf_mode}-{cid}-{uuid4()}",
                        workflow_mode=wf_mode,
                        transport="direct",
                    ),
                )
            process_one(bench_engine)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            wf_latencies.append(elapsed_ms)

            with readonly_session(bench_engine) as reader:
                actor = reader.get(User, "investigator")
                case = reader.get(Case, cid)
                run_row = reader.get(InvestigationRun, run_info["id"])
                if run_row.status != "awaiting_review":
                    failures += 1
                    if run_row.error_code and "TIMEOUT" in run_row.error_code:
                        timeouts += 1
                    outputs.append(
                        {
                            "case_id": cid,
                            "mode": wf_mode,
                            "status": run_row.status,
                            "error_code": run_row.error_code,
                            "latency_ms": elapsed_ms,
                        }
                    )
                    continue
                rep_row = reader.scalar(select(Report).where(Report.run_id == run_row.id))
                ev_rows = [
                    serialize(e)
                    for e in reader.scalars(select(Evidence).where(Evidence.run_id == run_row.id)).all()
                ]
                usage = rep_row.payload.get("usage", {})
                if rep_row.payload.get("provider_mode") != provider_mode:
                    raise ValueError("BENCHMARK_OUTPUT_MODE_MISMATCH")
                outputs.append(
                    {
                        "case_id": cid,
                        "mode": wf_mode,
                        "status": "completed",
                        "report": rep_row.payload,
                        "evidence": ev_rows,
                        "latency_ms": elapsed_ms,
                    }
                )
                wf_tools.append(int(usage.get("tool_calls", 0)))
                wf_llms.append(int(usage.get("llm_calls", 0)))
                wf_evals.append(
                    evaluate_case_report(reader, actor, case, rep_row.payload, ev_rows, gold_data[cid])
                )

        mode_summaries.append(
            aggregate_mode_metrics(
                wf_mode,
                wf_evals,
                wf_latencies,
                wf_tools,
                wf_llms,
                timeout_count=timeouts,
                failure_count=failures,
                seed=seed,
            )
        )

    bench_engine.dispose()
    # Keep each isolated experiment database/checkpoint for inspection; never reset shared files.

    artifact = {
        "experiment_id": f"exp-{split}-{provider_mode}-{seed}",
        "evaluation_tier": "offline_mock_plumbing" if provider_mode == "mock" else "live_synthetic",
        "disclaimer": (
            "Kết quả trên dữ liệu tổng hợp (synthetic). Chế độ mock chỉ kiểm chứng đường ống dữ liệu, "
            "phép tính và rubric A/B/C; KHÔNG đo trí tuệ LLM thật và KHÔNG ước lượng hiệu quả/ROI ngoài thực tế."
        ),
        "dataset": {
            "split": split,
            "seed": seed,
            "case_count": len(all_cases),
            "dataset_sha256": dataset_hash,
        },
        "configuration": {
            "provider_mode": provider_mode,
            "model_id": "deterministic-template-v1" if provider_mode == "mock" else settings.llm_model,
            "retrieval_mode": settings.retrieval_mode,
            "prompt_version": "tracelens-v2",
            "tool_version": "readonly-tools-v2",
            "budgets": {
                "max_tool_calls": settings.max_tool_calls,
                "max_llm_calls": settings.max_llm_calls,
                "max_repairs": settings.max_repairs,
                "wall_clock_timeout_seconds": settings.wall_clock_timeout_seconds,
            },
        },
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "modes": mode_summaries,
        "outputs": outputs,
        "limitations": [
            "Mock compares deterministic templates, not LLM reasoning or human performance.",
            "Baseline A is an unfilled template; quality scores are not a fair human-completed baseline.",
            "Dev and heldout share scenario families and rule-derived labels; not an independent reasoning test.",
            "Quality denominators include successful reports only; see attempted_cases and failed outputs.",
        ],
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    target_path = output_path or (RESULTS_DIR / f"benchmark_{split}_{provider_mode}.json")
    target_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TraceLens A/B/C Benchmark Runner")
    parser.add_argument("--split", choices=["demo", "dev", "heldout"], default="heldout")
    parser.add_argument("--seed", type=int, default=202)
    parser.add_argument("--limit", type=int, default=None, help="Optional subset size")
    parser.add_argument("--provider-mode", choices=["mock", "live"], default="mock")
    parser.add_argument("--allow-paid-live", action="store_true")
    parser.add_argument("--save-default-summary", action="store_true")
    args = parser.parse_args()

    result = run_benchmark_suite(
        split=args.split,
        seed=args.seed,
        limit=args.limit,
        provider_mode=args.provider_mode,
        allow_paid_live=args.allow_paid_live,
        output_path=(RESULTS_DIR / "mock_benchmark_summary.json") if args.save_default_summary else None,
    )
    print(
        json.dumps(
            {
                "experiment_id": result["experiment_id"],
                "split": result["dataset"]["split"],
                "case_count": result["dataset"]["case_count"],
                "modes": [
                    {
                        "mode": m["mode"],
                        "evidence_recall": m["evidence_recall"]["value"],
                        "counterevidence_recall": m["counterevidence_recall"]["value"],
                        "citation_validity": m["citation_validity"]["value"],
                        "recommendation_agreement": m["recommendation_agreement"]["value"],
                        "mean_tool_calls": m["efficiency"]["mean_tool_calls"],
                    }
                    for m in result["modes"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
