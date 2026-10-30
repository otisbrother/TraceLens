from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

from apps.api.config import settings
from apps.api.contracts import NextStepDecision, UsageProvenance
from apps.api.models import Case, User
from apps.api.providers import LiveLLMProvider, MockProvider
from apps.worker.main import process_one
from evaluation.runners.run_benchmark import run_benchmark_suite
from tests.conftest import login


def test_stop_does_not_read_uncollected_facts(client, database, monkeypatch):
    monkeypatch.setattr(MockProvider, "plan_next_step", lambda *args: NextStepDecision(
        hypothesis="Need more context", question="Stop for manual review?", tool_name="STOP",
        arguments={}, rationale="Stop without fetching device or trip tools", stop_condition="Manual review"))
    login(client)
    run = client.post("/api/cases/TL-1041/runs", json={"idempotency_key": str(uuid4())}).json()
    assert process_one(database)
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "awaiting_review"
    report = client.get("/api/cases/TL-1041/reports").json()[0]["payload"]
    assert report["recommendation"] == "INSUFFICIENT_EVIDENCE"
    assert "overlap_pairs" not in report["facts"]
    assert "trip_span_minutes" not in report["facts"]
    assert not report["counterevidence_claims"]
    for claim in report["supporting_claims"]:
        for eid in claim["evidence_ids"]:
            refs = client.get(f"/api/cases/TL-1041/evidence/{eid}").json()["source_refs"]
            assert not any(ref.startswith(("trip:", "usage:")) for ref in refs)


def test_parent_run_and_idempotency_cannot_change_scope_or_mode(client, database):
    login(client, "other-investigator")
    foreign = client.post("/api/cases/TL-1043/runs", json={"idempotency_key": str(uuid4())}).json()
    login(client)
    assert client.post("/api/cases/TL-1041/runs", json={"idempotency_key": str(uuid4()),
        "parent_run_id": foreign["id"]}).status_code == 404
    key = str(uuid4())
    assert client.post("/api/cases/TL-1041/runs", json={"idempotency_key": key, "workflow_mode": "B"}).status_code == 202
    assert client.post("/api/cases/TL-1041/runs", json={"idempotency_key": key, "workflow_mode": "C"}).status_code == 409


def test_failed_checkpoint_resume_is_visible_and_recovered(client, database, monkeypatch):
    from apps.worker import workflow
    login(client)
    run = client.post("/api/cases/TL-1041/runs", json={"idempotency_key": str(uuid4())}).json()
    process_one(database)
    report = client.get("/api/cases/TL-1041/reports").json()[0]
    login(client, "reviewer")
    original = workflow.resume_workflow_after_review
    def fail_resume(*args):
        raise OSError("simulated checkpoint unavailable")
    monkeypatch.setattr(workflow, "resume_workflow_after_review", fail_resume)
    body = {"idempotency_key": str(uuid4()), "expected_version": report["version"],
        "report_hash": report["content_hash"], "snapshot_id": report["snapshot_id"],
        "disposition": "REQUEST_MORE_INFORMATION", "rationale": "Cần thêm dữ liệu độc lập để đối chiếu."}
    decision = client.post("/api/cases/TL-1041/review", json=body)
    assert decision.status_code == 200
    status = client.get(f"/api/runs/{run['id']}").json()
    assert status["status"] == "review_pending_resume"
    assert status["error_code"] == "CHECKPOINT_RESUME_PENDING"
    monkeypatch.setattr(workflow, "resume_workflow_after_review", original)
    process_one(database)
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "completed"
    assert client.post("/api/cases/TL-1041/review", json=body).json()["id"] == decision.json()["id"]


def test_live_attempt_budget_and_invalid_response(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-placeholder")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(settings, "llm_max_retries", 3)
    calls = []
    def rate_limited(*args, **kwargs):
        calls.append(kwargs)
        return httpx.Response(429)
    monkeypatch.setattr(httpx, "post", rate_limited)
    usage = UsageProvenance(max_llm_calls=1)
    with pytest.raises(ValueError, match="LLM_BUDGET_EXCEEDED"):
        LiveLLMProvider().call_structured_json("Return JSON", {}, usage)
    assert len(calls) == 1 and usage.llm_calls == 1
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: httpx.Response(200, text="not JSON"))
    with pytest.raises(ValueError, match="PROVIDER_INVALID_JSON"):
        LiveLLMProvider().call_structured_json("Return JSON", {}, UsageProvenance())


def test_benchmark_preflight_blocks_mode_mismatch_and_unbounded_paid_runs(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    with pytest.raises(ValueError, match="MODE_MISMATCH"):
        run_benchmark_suite(provider_mode="mock", limit=1)
    with pytest.raises(ValueError, match="Batch live"):
        run_benchmark_suite(provider_mode="live", allow_paid_live=True)


def test_normal_seed_does_not_create_gold_files(database, tmp_path):
    from data.generator.seed import seed_database
    with Session(database) as db:
        seed_database(db, gold_output_dir=tmp_path / "gold", fixtures_output_dir=tmp_path / "fixtures")
        assert db.get(Case, "TL-1041") and db.get(User, "reviewer")
    assert not (tmp_path / "gold").exists()
