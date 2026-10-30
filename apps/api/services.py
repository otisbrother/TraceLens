from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from apps.api.config import settings
from apps.api.domain import require_case, serialize
from apps.api.models import (
    AuditEvent,
    Case,
    InvestigationRun,
    Job,
    Report,
    ReviewDecision,
    uid,
)


def audit(db, actor, action, case_id=None, run_id=None, payload=None):
    db.add(
        AuditEvent(
            tenant_id=actor.tenant_id,
            actor_id=actor.id,
            case_id=case_id,
            run_id=run_id,
            action=action,
            payload=payload or {},
        )
    )


def require_role(actor, *roles):
    if actor.role not in roles:
        raise HTTPException(403, "Vai trò không có quyền thực hiện thao tác này")


def start_run(db, actor, case_id, body):
    require_role(actor, "investigator")
    case = require_case(db, actor, case_id)
    previous = db.scalar(
        select(InvestigationRun).where(
            InvestigationRun.actor_id == actor.id,
            InvestigationRun.idempotency_key == body.idempotency_key,
        )
    )
    if previous:
        if (previous.case_id, previous.workflow_mode, previous.transport) != (
            case.id,
            body.workflow_mode,
            body.transport,
        ) or (body.parent_run_id and previous.parent_run_id != body.parent_run_id):
            raise HTTPException(409, "Idempotency key đã dùng với nội dung khác")
        return serialize(previous)

    prior_case_run = db.scalar(
        select(InvestigationRun)
        .where(InvestigationRun.case_id == case.id, InvestigationRun.tenant_id == actor.tenant_id)
        .order_by(InvestigationRun.created_at.desc())
        .limit(1)
    )
    parent_run_id = getattr(body, "parent_run_id", None) or (prior_case_run.id if prior_case_run else None)
    if parent_run_id:
        parent = db.scalar(
            select(InvestigationRun).where(
                InvestigationRun.id == parent_run_id,
                InvestigationRun.case_id == case.id,
                InvestigationRun.tenant_id == actor.tenant_id,
            )
        )
        if not parent:
            raise HTTPException(404, "Lượt điều tra cha không thuộc phạm vi hồ sơ")
    workflow_mode = getattr(body, "workflow_mode", "C")
    transport = getattr(body, "transport", "direct")

    changed = db.execute(
        update(Case)
        .where(
            Case.id == case.id,
            Case.tenant_id == actor.tenant_id,
            Case.status != "investigating",
        )
        .values(status="investigating", disposition=None)
    ).rowcount
    if not changed:
        db.rollback()
        raise HTTPException(409, "Hồ sơ đang có lượt điều tra; hãy theo dõi lượt hiện tại")

    run = InvestigationRun(
        id=uid(),
        case_id=case.id,
        tenant_id=actor.tenant_id,
        actor_id=actor.id,
        snapshot_id=case.snapshot_id,
        idempotency_key=body.idempotency_key,
        workflow_mode=workflow_mode,
        provider_mode=settings.llm_mode,
        retrieval_mode=settings.retrieval_mode,
        transport=transport,
        parent_run_id=parent_run_id,
        checkpoint_thread_id=None,
    )
    db.add(run)
    db.flush()
    db.add(Job(run_id=run.id))
    audit(
        db,
        actor,
        "investigation.queued",
        case.id,
        run.id,
        {
            "workflow_mode": workflow_mode,
            "provider_mode": settings.llm_mode,
            "retrieval_mode": settings.retrieval_mode,
            "transport": transport,
            "parent_run_id": parent_run_id,
        },
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Lượt điều tra trùng; đọc trạng thái rồi thử lại") from None
    return serialize(run)


def review_report(db, actor, case_id, body):
    require_role(actor, "reviewer")
    case = require_case(db, actor, case_id)
    previous = db.scalar(
        select(ReviewDecision).where(
            ReviewDecision.actor_id == actor.id,
            ReviewDecision.idempotency_key == body.idempotency_key,
        )
    )
    if previous:
        if (
            previous.case_id,
            previous.report_version,
            previous.report_hash,
            previous.snapshot_id,
            previous.disposition,
            previous.rationale,
        ) != (
            case.id,
            body.expected_version,
            body.report_hash,
            body.snapshot_id,
            body.disposition,
            body.rationale,
        ):
            raise HTTPException(409, "Idempotency key không khớp nội dung đã gửi")
        _resume_review(db, actor, previous)
        return serialize(previous)
    report = db.scalar(
        select(Report).where(Report.case_id == case.id, Report.version == body.expected_version)
    )
    if not report or (report.content_hash, report.snapshot_id) != (
        body.report_hash,
        body.snapshot_id,
    ):
        raise HTTPException(409, "Báo cáo hoặc snapshot không khớp; tải lại hồ sơ")
    changed = db.execute(
        update(Case)
        .where(
            Case.id == case.id,
            Case.tenant_id == actor.tenant_id,
            Case.report_version == body.expected_version,
            Case.snapshot_id == body.snapshot_id,
            Case.status == "awaiting_review",
        )
        .values(
            status=("needs_information" if body.disposition == "REQUEST_MORE_INFORMATION" else "reviewed"),
            disposition=body.disposition,
        )
    ).rowcount
    if not changed:
        db.rollback()
        raise HTTPException(409, "Phiên bản cũ hoặc hồ sơ không còn chờ duyệt")
    decision = ReviewDecision(
        id=uid(),
        case_id=case.id,
        report_id=report.id,
        report_version=report.version,
        report_hash=report.content_hash,
        snapshot_id=report.snapshot_id,
        actor_id=actor.id,
        idempotency_key=body.idempotency_key,
        disposition=body.disposition,
        rationale=body.rationale,
    )
    db.add(decision)
    run = db.get(InvestigationRun, report.run_id)
    run.status = "review_pending_resume"
    audit(
        db,
        actor,
        "review.recorded",
        case.id,
        run.id,
        {
            "decision_id": decision.id,
            "version": report.version,
            "report_hash": report.content_hash,
            "disposition": body.disposition,
            "snapshot_id": report.snapshot_id,
        },
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Quyết định đã được ghi; tải lại hồ sơ") from None

    _resume_review(db, actor, decision)
    return serialize(decision)


def _resume_review(db, actor, decision):
    run = db.scalar(
        select(InvestigationRun)
        .join(Report, Report.run_id == InvestigationRun.id)
        .where(Report.id == decision.report_id)
    )
    if run.status == "completed":
        return
    try:
        from apps.worker.workflow import resume_workflow_after_review

        resume_workflow_after_review(db.get_bind(), run.id, serialize(decision))
    except Exception:
        if run.error_code != "CHECKPOINT_RESUME_PENDING":
            run.error_code = "CHECKPOINT_RESUME_PENDING"
            audit(db, actor, "review.resume_pending", run.case_id, run.id)
            db.commit()
        return
    run.status, run.error_code = "completed", None
    db.commit()
