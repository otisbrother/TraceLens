"""Separate durable worker running bounded LangGraph investigation workflows."""

import argparse
import time
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from apps.api.config import settings
from apps.api.contracts import StartInput
from apps.api.db import engine
from apps.api.models import Case, InvestigationRun, Job, Report, ReviewDecision, User, uid
from apps.api.services import _resume_review, audit, start_run
from apps.worker.workflow import run_workflow_until_review

MAX_ATTEMPTS = 3
LEASE_SECONDS = 60

TERMINAL_ERROR_CODES = {
    "UNSUPPORTED_MODE",
    "LIVE_PROVIDER_NOT_CONFIGURED",
    "EMBEDDING_PROVIDER_NOT_CONFIGURED",
    "INVALID_RETRIEVAL_MODE",
    "PROVIDER_INVALID_JSON",
    "PROVIDER_INVALID_SCHEMA",
    "PROVIDER_REFUSAL",
    "PROVIDER_TIMEOUT",
    "PROVIDER_RATE_LIMIT",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_NETWORK_ERROR",
    "LLM_BUDGET_EXCEEDED",
    "TOKEN_BUDGET_EXCEEDED",
    "TOOL_BUDGET_EXCEEDED",
    "WALL_CLOCK_TIMEOUT",
    "ACTOR_REVOKED",
    "SNAPSHOT_CHANGED",
    "VALIDATION_FAILED",
    "ACTOR_OR_SNAPSHOT_CHANGED",
}


def resolve_provider_mode() -> tuple[str, str]:
    if settings.retrieval_mode not in {"lexical", "hybrid"}:
        raise ValueError("UNSUPPORTED_MODE")
    if settings.retrieval_mode == "hybrid" and (
        not settings.embedding_api_key or not settings.embedding_model
    ):
        raise ValueError("EMBEDDING_PROVIDER_NOT_CONFIGURED")

    if settings.llm_mode == "mock":
        return "mock", "deterministic-template-v1"
    if settings.llm_mode == "live":
        if settings.llm_api_key and settings.llm_model:
            return "live", settings.llm_model
        if settings.llm_allow_degraded_fallback:
            return "degraded", "degraded-mock-fallback-v1"
        raise ValueError("LIVE_PROVIDER_NOT_CONFIGURED")
    raise ValueError("UNSUPPORTED_MODE")


def claim_job(database_engine):
    now = int(time.time())
    with Session(database_engine) as db:
        eligible = or_(Job.status == "queued", and_(Job.status == "running", Job.lease_until < now))
        # A crashed final attempt must terminate instead of remaining stuck forever.
        exhausted = db.scalars(select(Job).where(eligible, Job.attempts >= MAX_ATTEMPTS)).all()
        for job in exhausted:
            changed = db.execute(
                update(Job)
                .where(Job.id == job.id, eligible, Job.attempts >= MAX_ATTEMPTS)
                .values(status="failed", lease_token=None, lease_until=0)
            ).rowcount
            if changed:
                run = db.get(InvestigationRun, job.run_id)
                run.status, run.error_code = "failed", "RETRY_EXHAUSTED"
                db.get(Case, run.case_id).status = "needs_manual_review"
                audit(
                    db,
                    db.get(User, run.actor_id),
                    "investigation.failed",
                    run.case_id,
                    run.id,
                    {"error_code": "RETRY_EXHAUSTED"},
                )
        db.commit()
        candidate = db.scalar(
            select(Job.id).where(eligible, Job.attempts < MAX_ATTEMPTS).order_by(Job.id).limit(1)
        )
        if not candidate:
            return None
        token = uid()
        changed = db.execute(
            update(Job)
            .where(Job.id == candidate, eligible, Job.attempts < MAX_ATTEMPTS)
            .values(
                status="running",
                lease_until=now + LEASE_SECONDS,
                lease_token=token,
                attempts=Job.attempts + 1,
            )
        ).rowcount
        db.commit()
        if not changed:
            return None
        job = db.get(Job, candidate)
        run = db.get(InvestigationRun, job.run_id)
        run.status = "running"
        audit(
            db,
            db.get(User, run.actor_id),
            "investigation.started",
            run.case_id,
            run.id,
            {
                "attempt": job.attempts,
                "provider_mode": run.provider_mode,
                "workflow_mode": run.workflow_mode,
                "transport": run.transport,
            },
        )
        db.commit()
        return candidate, token, job.run_id


def process_one(database_engine=engine) -> bool:
    with Session(database_engine) as db:
        pending = db.scalars(
            select(ReviewDecision)
            .join(Report, Report.id == ReviewDecision.report_id)
            .join(InvestigationRun, InvestigationRun.id == Report.run_id)
            .where(InvestigationRun.status == "review_pending_resume")
            .limit(20)
        ).all()
        for decision in pending:
            _resume_review(db, db.get(User, decision.actor_id), decision)
    claimed = claim_job(database_engine)
    if not claimed:
        return False
    job_id, token, run_id = claimed
    try:
        provider_mode, model_id = resolve_provider_mode()
        with Session(database_engine) as db:
            run = db.get(InvestigationRun, run_id)
            if run and run.provider_mode != provider_mode:
                run.provider_mode = provider_mode
                db.commit()
        run_workflow_until_review(
            database_engine,
            run_id=run_id,
            job_id=job_id,
            lease_token=token,
            provider_mode=provider_mode,
            model_id=model_id,
        )
    except Exception as exc:
        raw_msg = str(exc)
        code = (
            raw_msg
            if isinstance(exc, ValueError)
            and (raw_msg in TERMINAL_ERROR_CODES or raw_msg.startswith("PROVIDER_HTTP_"))
            else "WORKER_ERROR"
        )
        with Session(database_engine) as db:
            job = db.scalar(select(Job).where(Job.id == job_id, Job.lease_token == token))
            if job:
                terminal = job.attempts >= MAX_ATTEMPTS or code != "WORKER_ERROR"
                job.status, job.lease_token, job.lease_until = "failed" if terminal else "queued", None, 0
                run = db.get(InvestigationRun, run_id)
                run.status, run.error_code = "failed" if terminal else "queued", code
                if terminal:
                    db.get(Case, run.case_id).status = "needs_manual_review"
                audit(
                    db,
                    db.get(User, run.actor_id),
                    "investigation.failed" if terminal else "investigation.retry",
                    run.case_id,
                    run.id,
                    {"error_code": code, "attempt": job.attempts, "provider_mode": run.provider_mode},
                )
                db.commit()
    return True


def run_single_case_cli(
    case_id: str,
    workflow_mode: str = "C",
    transport: str = "direct",
    database_engine=engine,
) -> dict:
    with Session(database_engine) as db:
        actor = db.get(User, "investigator")
        if not actor:
            raise SystemExit("Chưa có tài khoản investigator; hãy chạy seed trước.")
        case = db.get(Case, case_id)
        if not case:
            raise SystemExit(f"Không tìm thấy hồ sơ {case_id}")
        if case.status == "investigating":
            raise SystemExit(
                "Hồ sơ đang có lượt chạy; không tạo thêm job. Dùng worker --once để tiếp tục hàng đợi."
            )
        run_info = start_run(
            db,
            actor,
            case_id,
            StartInput(
                idempotency_key=f"cli-{case_id}-{uuid4()}",
                workflow_mode=workflow_mode,
                transport=transport,
            ),
        )
    process_one(database_engine)
    with Session(database_engine) as db:
        updated = db.get(InvestigationRun, run_info["id"])
        return {
            "run_id": updated.id,
            "case_id": updated.case_id,
            "status": updated.status,
            "workflow_mode": updated.workflow_mode,
            "provider_mode": updated.provider_mode,
            "transport": updated.transport,
            "error_code": updated.error_code,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TraceLens durable LangGraph worker")
    parser.add_argument("--once", action="store_true", help="Process one queued/recoverable job and exit")
    parser.add_argument("--case", type=str, default="", help="Queue and process a specific case_id")
    parser.add_argument("--mode", choices=["B", "C"], default="C", help="Workflow mode (B or C)")
    parser.add_argument("--transport", choices=["direct", "mcp"], default="direct")
    args = parser.parse_args()
    if args.case:
        print(run_single_case_cli(args.case, workflow_mode=args.mode, transport=args.transport))
    elif args.once:
        print({"processed": process_one()})
    else:
        print("TraceLens worker: LangGraph / durable DB queue", flush=True)
        while True:
            if not process_one():
                time.sleep(0.5)
