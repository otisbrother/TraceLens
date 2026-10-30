import json
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from apps.api.config import settings
from apps.api.contracts import LoginInput, ReviewInput, StartInput
from apps.api.db import get_db
from apps.api.domain import (
    gather_facts,
    get_source,
    readonly_session,
    require_case,
    serialize,
    tool_get_relationship_subgraph,
)
from apps.api.models import (
    Alert,
    AuditEvent,
    Case,
    Evidence,
    InvestigationRun,
    LoginSession,
    Report,
    ReviewDecision,
    User,
)
from apps.api.security import check_password, current_actor, token_hash
from apps.api.services import audit, review_report, start_run
from apps.worker.workflow import get_workflow_checkpoint_state

app = FastAPI(title="TraceLens", version="0.2.0")


@app.middleware("http")
async def origin_and_headers(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in settings.allowed_origins:
            return JSONResponse({"detail": "Origin không được phép"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site request bị từ chối"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health():
    return {"status": "alive"}


@app.get("/api/ready")
def ready(db: Session = Depends(get_db)):
    try:
        db.execute(select(Case.id).limit(1))
        db.execute(text("SELECT version_num FROM alembic_version"))
    except Exception:
        raise HTTPException(503, "Database chưa sẵn sàng; chạy migration") from None
    if settings.llm_mode not in {"mock", "live"}:
        raise HTTPException(503, "LLM_MODE không hợp lệ")
    if settings.llm_mode == "live" and (not settings.llm_api_key or not settings.llm_model):
        if not settings.llm_allow_degraded_fallback:
            raise HTTPException(
                503, "LLM_MODE=live yêu cầu LLM_API_KEY và LLM_MODEL; không tự động đổi sang mock"
            )
    if settings.retrieval_mode not in {"lexical", "hybrid"}:
        raise HTTPException(503, "RETRIEVAL_MODE không hợp lệ")
    if settings.retrieval_mode == "hybrid" and (
        not settings.embedding_api_key or not settings.embedding_model
    ):
        raise HTTPException(
            503, "RETRIEVAL_MODE=hybrid yêu cầu EMBEDDING_API_KEY và EMBEDDING_MODEL"
        )
    return {
        "status": "ready",
        "provider_mode": settings.llm_mode,
        "retrieval_mode": settings.retrieval_mode,
        "tool_transport": settings.tool_transport,
        "synthetic": True,
    }


def actor_view(actor):
    return {
        "id": actor.id,
        "username": actor.username,
        "role": actor.role,
        "tenant_id": actor.tenant_id,
    }


@app.post("/api/session/login")
def login(body: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    actor = db.scalar(select(User).where(User.username == body.username, User.active.is_(True)))
    if not actor or not check_password(body.password, actor.password_hash):
        raise HTTPException(401, "Tên đăng nhập hoặc mật khẩu không đúng")
    previous = db.get(LoginSession, token_hash(request.cookies.get("tracelens_session", "")))
    if previous:
        db.delete(previous)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            token_hash=token_hash(token),
            user_id=actor.id,
            csrf_token=csrf,
            expires_at=int(time.time()) + 8 * 3600,
        )
    )
    audit(db, actor, "session.login")
    db.commit()
    response.set_cookie(
        "tracelens_session",
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=8 * 3600,
        path="/",
    )
    return {"actor": actor_view(actor), "csrf_token": csrf}


@app.get("/api/session")
def session_info(request: Request, actor: User = Depends(current_actor)):
    return {
        "actor": actor_view(actor),
        "csrf_token": request.state.login_session.csrf_token,
        "runtime": {
            "provider_mode": settings.llm_mode,
            "retrieval_mode": settings.retrieval_mode,
            "tool_transport": settings.tool_transport,
            "synthetic": True,
        },
    }


@app.post("/api/session/logout")
def logout(
    request: Request,
    response: Response,
    actor: User = Depends(current_actor),
    db: Session = Depends(get_db),
):
    db.delete(request.state.login_session)
    audit(db, actor, "session.logout")
    db.commit()
    response.delete_cookie("tracelens_session", path="/")
    return {"ok": True}


@app.get("/api/cases")
def list_cases(
    q: str = Query("", max_length=80),
    status: str = "",
    priority_band: str = "",
    actor: User = Depends(current_actor),
    db: Session = Depends(get_db),
):
    query = select(Case).where(Case.tenant_id == actor.tenant_id)
    if q:
        query = query.where(Case.id.contains(q, autoescape=True))
    if status:
        query = query.where(Case.status == status)
    result = []
    for case in db.scalars(query.order_by(Case.id).limit(100)):
        alert = db.scalar(select(Alert).where(Alert.case_id == case.id))
        if priority_band and alert and alert.priority_band != priority_band:
            continue
        result.append({**serialize(case), "alert": serialize(alert) if alert else {}})
    return result


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str, actor: User = Depends(current_actor), db: Session = Depends(get_db)):
    require_case(db, actor, case_id)
    # End auth transaction before opening an independent read-only service transaction.
    with readonly_session(db.get_bind()) as reader:
        return gather_facts(reader, actor, case_id).model_dump()


@app.get("/api/cases/{case_id}/subgraph")
def case_subgraph(
    case_id: str,
    max_hops: int = Query(2, ge=1, le=3),
    actor: User = Depends(current_actor),
    db: Session = Depends(get_db),
):
    require_case(db, actor, case_id)
    with readonly_session(db.get_bind()) as reader:
        return tool_get_relationship_subgraph(reader, actor, case_id, max_hops=max_hops).model_dump()


@app.get("/api/cases/{case_id}/sources/{source_ref}")
def source_record(
    case_id: str,
    source_ref: str,
    actor: User = Depends(current_actor),
    db: Session = Depends(get_db),
):
    return get_source(db, actor, case_id, source_ref)


@app.get("/api/cases/{case_id}/baseline")
def baseline(case_id: str, actor: User = Depends(current_actor), db: Session = Depends(get_db)):
    with readonly_session(db.get_bind()) as reader:
        envelope = gather_facts(reader, actor, case_id)
    return {
        "mode": "A",
        "provider_mode": "none",
        "facts": envelope.data["facts"],
        "source_refs": envelope.source_record_ids,
        "policies": envelope.data["policies"],
        "template": {
            "observations": "",
            "supporting_evidence": "",
            "counterevidence": "",
            "missing_information": "",
            "proposed_disposition": "",
        },
    }


@app.post("/api/cases/{case_id}/runs", status_code=202)
def create_run(
    case_id: str,
    body: StartInput,
    actor: User = Depends(current_actor),
    db: Session = Depends(get_db),
):
    if settings.llm_mode == "live" and (not settings.llm_api_key or not settings.llm_model):
        if not settings.llm_allow_degraded_fallback:
            raise HTTPException(
                503,
                "LLM_MODE=live yêu cầu LLM_API_KEY và LLM_MODEL được cấu hình ở server; không tự ý đổi sang mock",
            )
    if settings.retrieval_mode == "hybrid" and (
        not settings.embedding_api_key or not settings.embedding_model
    ):
        raise HTTPException(
            503, "RETRIEVAL_MODE=hybrid yêu cầu EMBEDDING_API_KEY và EMBEDDING_MODEL"
        )
    return start_run(db, actor, case_id, body)


@app.get("/api/cases/{case_id}/runs")
def case_runs(case_id: str, actor: User = Depends(current_actor), db: Session = Depends(get_db)):
    require_case(db, actor, case_id)
    return [
        serialize(r)
        for r in db.scalars(
            select(InvestigationRun)
            .where(InvestigationRun.case_id == case_id, InvestigationRun.tenant_id == actor.tenant_id)
            .order_by(InvestigationRun.created_at.desc())
            .limit(50)
        )
    ]


@app.get("/api/runs/{run_id}")
def run_status(run_id: str, actor: User = Depends(current_actor), db: Session = Depends(get_db)):
    run = db.get(InvestigationRun, run_id)
    if not run:
        raise HTTPException(404, "Không tìm thấy lượt điều tra")
    require_case(db, actor, run.case_id)
    return serialize(run)


@app.get("/api/runs/{run_id}/checkpoint")
def run_checkpoint(run_id: str, actor: User = Depends(current_actor), db: Session = Depends(get_db)):
    run_status(run_id, actor, db)
    return get_workflow_checkpoint_state(db.get_bind(), run_id)


@app.get("/api/runs/{run_id}/events")
def run_events(
    run_id: str,
    request: Request,
    after: int = Query(0, ge=0),
    actor: User = Depends(current_actor),
    db: Session = Depends(get_db),
):
    run_status(run_id, actor, db)
    try:
        cursor = max(after, int(request.headers.get("Last-Event-ID", "0")))
    except ValueError:
        raise HTTPException(400, "Last-Event-ID không hợp lệ") from None
    events = [
        serialize(e)
        for e in db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.run_id == run_id,
                AuditEvent.tenant_id == actor.tenant_id,
                AuditEvent.id > cursor,
            )
            .order_by(AuditEvent.id)
            .limit(100)
        )
    ]
    # Bounded durable replay; EventSource reconnects or the UI polls the same stored state.
    body = "retry: 2000\n\n" + "".join(
        f"id: {e['id']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events
    )
    return StreamingResponse(iter([body]), media_type="text/event-stream")


@app.get("/api/cases/{case_id}/reports")
def reports(case_id: str, actor: User = Depends(current_actor), db: Session = Depends(get_db)):
    require_case(db, actor, case_id)
    return [
        serialize(r)
        for r in db.scalars(
            select(Report)
            .where(Report.case_id == case_id, Report.tenant_id == actor.tenant_id)
            .order_by(Report.version.desc())
        )
    ]


@app.get("/api/cases/{case_id}/evidence/{evidence_id}")
def evidence_record(
    case_id: str,
    evidence_id: str,
    actor: User = Depends(current_actor),
    db: Session = Depends(get_db),
):
    case = require_case(db, actor, case_id)
    row = db.scalar(
        select(Evidence).where(
            Evidence.id == evidence_id,
            Evidence.case_id == case.id,
            Evidence.tenant_id == actor.tenant_id,
            Evidence.snapshot_id == case.snapshot_id,
        )
    )
    if not row:
        raise HTTPException(404, "Bằng chứng không thuộc phạm vi hồ sơ")
    return serialize(row)


@app.post("/api/cases/{case_id}/review")
def submit_review(
    case_id: str,
    body: ReviewInput,
    actor: User = Depends(current_actor),
    db: Session = Depends(get_db),
):
    return review_report(db, actor, case_id, body)


@app.get("/api/cases/{case_id}/audit")
def case_audit(case_id: str, actor: User = Depends(current_actor), db: Session = Depends(get_db)):
    require_case(db, actor, case_id)
    return [
        serialize(e)
        for e in db.scalars(
            select(AuditEvent)
            .where(AuditEvent.case_id == case_id, AuditEvent.tenant_id == actor.tenant_id)
            .order_by(AuditEvent.id)
        )
    ]


@app.get("/api/cases/{case_id}/export")
def export_case(
    case_id: str,
    format: str = Query("json", pattern="^(json|markdown)$"),
    actor: User = Depends(current_actor),
    db: Session = Depends(get_db),
):
    case = require_case(db, actor, case_id)
    payload = {
        "synthetic": True,
        "case": serialize(case),
        "reports": reports(case_id, actor, db),
        "evidence": [
            serialize(e)
            for e in db.scalars(
                select(Evidence).where(
                    Evidence.case_id == case_id, Evidence.tenant_id == actor.tenant_id
                )
            )
        ],
        "decisions": [
            serialize(d)
            for d in db.scalars(select(ReviewDecision).where(ReviewDecision.case_id == case_id))
        ],
        "audit": case_audit(case_id, actor, db),
    }
    audit(db, actor, "case.exported", case_id, payload={"format": format})
    db.commit()
    if format == "markdown":
        body = f"# TraceLens · {case_id}\n\nDữ liệu tổng hợp / {settings.llm_mode} / UTC.\n\n"
        for report in payload["reports"]:
            p = report["payload"]
            body += f"## Báo cáo v{report['version']}\n\n{p['summary']}\n\n{p['recommendation']}\n\n"
            for key in ["supporting_claims", "counterevidence_claims", "missing_information"]:
                body += f"### {key}\n\n"
                body += (
                    "\n".join(
                        f"- {c['text']} [{', '.join(c['evidence_ids'])}]" for c in p[key]
                    )
                    + "\n\n"
                )
        body += (
            "## Hồ sơ và nguồn có cấu trúc\n\n```json\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + "\n```\n"
        )
        return PlainTextResponse(
            body, headers={"Content-Disposition": f'attachment; filename="{case_id}.md"'}
        )
    return JSONResponse(
        payload, headers={"Content-Disposition": f'attachment; filename="{case_id}.json"'}
    )


@app.get("/api/evaluation")
def evaluation(actor: User = Depends(current_actor)):
    artifact_file = Path(settings.benchmark_artifact_path)
    mock_benchmark = None
    if artifact_file.exists():
        try:
            mock_benchmark = json.loads(artifact_file.read_text(encoding="utf-8"))
        except Exception:
            mock_benchmark = None
    return {
        "status": "not_run",
        "mock_plumbing": "completed" if mock_benchmark else "see docs/IMPLEMENTATION_STATUS.md",
        "live_model": "not_run",
        "human_study": "not_run",
        "results": mock_benchmark.get("modes", []) if mock_benchmark else [],
        "mock_benchmark": mock_benchmark,
        "note": (
            "Chưa chạy benchmark với Live LLM hoặc Human Study (status=not_run). "
            "Kết quả kèm theo (nếu có) là kiểm chứng đường ống A/B/C ở chế độ offline mock trên dữ liệu tổng hợp."
        ),
    }


web_dist = Path(__file__).resolve().parents[1] / "web" / "dist"
if web_dist.is_dir():
    app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
