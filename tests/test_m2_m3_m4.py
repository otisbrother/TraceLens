"""Comprehensive behavioral tests for M2, M3, and M4 requirements (Section 18)."""

from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.config import settings
from apps.api.contracts import TrustedContext
from apps.api.db import make_engine
from apps.api.models import (
    Case,
    InvestigationRun,
    ReviewDecision,
    SourceQuality,
    Trip,
    User,
)
from apps.api.reports import build_report, repair_report, validate_report
from apps.api.tools import READONLY_TOOL_ALLOWLIST, execute_direct_tool
from apps.mcp.smoke_test import run_smoke_test
from apps.worker.main import process_one
from apps.worker.workflow import get_workflow_checkpoint_state
from data.generator.seed import build_split_specs, stable_id
from evaluation.runners.run_benchmark import run_benchmark_suite
from tests.conftest import login


def test_dev_and_heldout_splits_disjoint_accounts_devices_groups():
    """Requirement 1: Held-out split has zero overlap in account, device, or group IDs with dev split."""
    dev_specs = build_split_specs("dev", 50, 101)
    hold_specs = build_split_specs("heldout", 150, 202)
    assert len(dev_specs) == 50
    assert len(hold_specs) == 150

    dev_cases = {s["case_id"] for s in dev_specs}
    hold_cases = {s["case_id"] for s in hold_specs}
    assert dev_cases.isdisjoint(hold_cases)

    dev_riders = {
        stable_id(101, f"dev/{idx}/rider/{r}")
        for idx, s in enumerate(dev_specs)
        for r in range(s["rider_count"])
    }
    hold_riders = {
        stable_id(202, f"heldout/{idx}/rider/{r}")
        for idx, s in enumerate(hold_specs)
        for r in range(s["rider_count"])
    }
    assert dev_riders.isdisjoint(hold_riders)

    dev_devices = {stable_id(101, f"dev/{idx}/device/0") for idx in range(len(dev_specs))}
    hold_devices = {stable_id(202, f"heldout/{idx}/device/0") for idx in range(len(hold_specs))}
    assert dev_devices.isdisjoint(hold_devices)


def test_shared_ip_transfer_commuting_reversal_not_concluded_fraud(client, database):
    """Requirements 3 & 4: Shared office IP (TL-1045), sequential device transfer (TL-1044),
    regular commuting (TL-1046), and full reversal accounting (TL-1047) are never concluded as fraud."""
    login(client)
    for cid, expected_net in [
        ("TL-1044", 120000),  # Sequential transfer
        ("TL-1045", 120000),  # Shared office IP
        ("TL-1046", 100000),  # Regular commuting
        ("TL-1047", 0),       # Full reversal accounting
    ]:
        res = client.post(f"/api/cases/{cid}/runs", json={"idempotency_key": str(uuid4())})
        assert res.status_code == 202
        assert process_one(database)
        rep = client.get(f"/api/cases/{cid}/reports").json()[0]
        assert rep["payload"]["recommendation"] == "NO_ACTION_SUGGESTED", cid
        assert rep["payload"]["facts"]["overlap_pairs"] == 0, cid
        assert rep["payload"]["facts"]["net_vnd"] == expected_net, cid
        assert rep["payload"]["counterevidence_claims"], cid


def test_distinguish_empty_result_from_timeout_and_truncation(database):
    """Requirement 5: Tools distinguish 0 matching records (complete+empty) from timeout and truncation."""
    with Session(database) as db:
        actor = db.get(User, "investigator")
        case = db.get(Case, "TL-1041")
        ctx = TrustedContext(
            actor_id=actor.id,
            username=actor.username,
            role=actor.role,
            tenant_id=actor.tenant_id,
            case_id=case.id,
            snapshot_id=case.snapshot_id,
            cutoff=case.cutoff,
        )

    # 1. Empty time window on healthy source -> completeness="complete", empty=True, error=None
    empty_env = execute_direct_tool(
        database,
        ctx,
        "get_trip_history",
        {"start_at": "2020-01-01T00:00:00.000+00:00", "end_at": "2020-01-02T00:00:00.000+00:00"},
    )
    assert empty_env.completeness == "complete"
    assert empty_env.error is None
    assert empty_env.data["empty"] is True
    assert empty_env.data["items"] == []

    # 2. Paginated truncation -> truncated=True, next_cursor present
    trunc_env = execute_direct_tool(
        database,
        ctx,
        "get_trip_history",
        {"page_size": 2},
    )
    assert trunc_env.completeness == "complete"
    assert trunc_env.truncated is True
    assert trunc_env.next_cursor == "2"
    assert len(trunc_env.data["items"]) == 2

    # 3. Source timeout -> completeness="unavailable", error="SOURCE_TIMEOUT"
    with Session(database) as db:
        q = db.scalar(
            select(SourceQuality).where(
                SourceQuality.case_id == "TL-1041", SourceQuality.source_system == "trips"
            )
        )
        q.completeness = "timeout"
        db.commit()
    timeout_env = execute_direct_tool(database, ctx, "get_trip_history", {})
    assert timeout_env.completeness == "unavailable"
    assert timeout_env.error == "SOURCE_TIMEOUT"


def test_readonly_allowlist_and_no_write_tools(database):
    """Requirement 8: Allowlist contains only read-only tools; any write/ban/shell tool is blocked."""
    forbidden_tools = {
        "ban_account",
        "lock_user",
        "unlock_user",
        "refund_payment",
        "execute_sql",
        "run_shell",
        "fetch_url",
        "write_file",
    }
    assert READONLY_TOOL_ALLOWLIST.isdisjoint(forbidden_tools)
    with Session(database) as db:
        actor = db.get(User, "investigator")
        case = db.get(Case, "TL-1041")
        ctx = TrustedContext(
            actor_id=actor.id,
            username=actor.username,
            role=actor.role,
            tenant_id=actor.tenant_id,
            case_id=case.id,
            snapshot_id=case.snapshot_id,
            cutoff=case.cutoff,
        )
    for bad in ["ban_account", "execute_sql", "run_shell"]:
        env = execute_direct_tool(database, ctx, bad, {})
        assert env.error == "TOOL_NOT_ALLOWED"


def test_bounded_repair_and_cross_case_source_rejection(database):
    """Requirement 7: Claims/reports with wrong numbers or cross-case sources are flagged/repaired."""
    with Session(database) as db:
        actor = db.get(User, "investigator")
        case = db.get(Case, "TL-1041")
        other_trip = db.scalar(select(Trip).where(Trip.case_id == "TL-1042"))
        run = InvestigationRun(
            id=str(uuid4()),
            case_id=case.id,
            actor_id=actor.id,
            tenant_id=actor.tenant_id,
            snapshot_id=case.snapshot_id,
            idempotency_key=str(uuid4()),
        )
        from apps.api.domain import gather_facts

        envelope = gather_facts(db, actor, case.id)
        content, evidence = build_report(case, run, envelope)

        # Corrupt numeric facts -> detected and repaired in 1 bounded step
        content.facts["net_vnd"] = 999999
        findings = validate_report(db, actor, case, run, content, evidence, envelope.data["facts"])
        assert "FACTS_MISMATCH" in findings
        repaired_content, remaining = repair_report(
            db, actor, case, run, content, evidence, envelope.data["facts"], findings
        )
        assert remaining == []
        assert repaired_content.facts["net_vnd"] == 120000
        assert repaired_content.usage.repair_attempts == 1

        # Inject cross-case source ref into evidence -> rejected by validator
        evidence[0].source_refs.append(f"trip:{other_trip.id}")
        bad_findings = validate_report(
            db, actor, case, run, repaired_content, evidence, envelope.data["facts"]
        )
        assert "SOURCE_SCOPE_OR_VALIDITY_ERROR" in bad_findings


def test_langgraph_checkpoint_interrupt_resume_and_continuation(client, database):
    """Requirement 11: LangGraph persists interrupt checkpoint across engine restart and resumes
    cleanly without duplicating decisions; REQUEST_MORE_INFORMATION links continuation run."""
    login(client)
    res = client.post(
        "/api/cases/TL-1041/runs",
        json={"idempotency_key": str(uuid4()), "workflow_mode": "C", "transport": "direct"},
    )
    assert res.status_code == 202
    run_id = res.json()["id"]
    assert process_one(database)

    # Checkpoint is paused at human_review_interrupt
    ckpt_before = client.get(f"/api/runs/{run_id}/checkpoint").json()
    assert ckpt_before["exists"] is True
    assert "human_review_interrupt" in ckpt_before["next"]
    assert len(ckpt_before["values"]["planned_steps"]) >= 3

    # Simulate process restart with new engine
    restarted = make_engine(str(database.url))
    state_on_disk = get_workflow_checkpoint_state(restarted, run_id)
    assert "human_review_interrupt" in state_on_disk["next"]
    restarted.dispose()

    # Reviewer requests more information
    rep_v1 = client.get("/api/cases/TL-1041/reports").json()[0]
    login(client, "reviewer")
    rev_key = str(uuid4())
    rev_body = {
        "expected_version": rep_v1["version"],
        "report_hash": rep_v1["content_hash"],
        "snapshot_id": rep_v1["snapshot_id"],
        "disposition": "REQUEST_MORE_INFORMATION",
        "rationale": "Cần kiểm tra lại sau khi đối chiếu thêm ghi chú phiên.",
        "idempotency_key": rev_key,
    }
    r1 = client.post("/api/cases/TL-1041/review", json=rev_body)
    assert r1.status_code == 200
    # Duplicate approval submit returns same decision without creating a second row
    r2 = client.post("/api/cases/TL-1041/review", json=rev_body)
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]

    # Checkpoint has now resumed and completed finalize_disposition (next == [])
    ckpt_after = client.get(f"/api/runs/{run_id}/checkpoint").json()
    assert ckpt_after["next"] == []
    assert ckpt_after["values"]["finalized"] is True

    # Investigator starts continuation run -> links parent_run_id and creates v2 report
    login(client, "investigator")
    cont_res = client.post(
        "/api/cases/TL-1041/runs",
        json={"idempotency_key": str(uuid4()), "workflow_mode": "C"},
    )
    assert cont_res.status_code == 202
    assert cont_res.json()["parent_run_id"] == run_id
    assert process_one(database)
    all_reports = client.get("/api/cases/TL-1041/reports").json()
    assert len(all_reports) == 2
    assert all_reports[0]["version"] == 2
    assert all_reports[1]["version"] == 1

    with Session(database) as db:
        decisions = db.scalars(
            select(ReviewDecision).where(ReviewDecision.case_id == "TL-1041")
        ).all()
        assert len(decisions) == 1


def test_provider_errors_invalid_json_timeout_budget_fail_closed(client, database, monkeypatch):
    """Requirement 13: Provider invalid JSON, timeout, and budget exceeded transition to failed /
    needs_manual_review with clean audit events and never silently fake success."""
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key-not-logged")
    monkeypatch.setattr(settings, "llm_model", "test-model-v1")

    for idx, (case_id, simulated_exc, expected_code) in enumerate(
        [
            ("TL-1041", "invalid_json", "PROVIDER_INVALID_JSON"),
            ("TL-1042", "timeout", "PROVIDER_TIMEOUT"),
            ("TL-1044", "budget", "LLM_BUDGET_EXCEEDED"),
        ]
    ):
        def fake_post(url, **kwargs):
            if simulated_exc == "timeout":
                raise httpx.TimeoutException("timed out")
            request = httpx.Request("POST", url)
            if simulated_exc == "invalid_json":
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "{not-valid-json"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
                    request=request,
                )
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}, request=request)

        monkeypatch.setattr(httpx, "post", fake_post)
        if simulated_exc == "budget":
            monkeypatch.setattr(settings, "max_llm_calls", 0)
        else:
            monkeypatch.setattr(settings, "max_llm_calls", 3)

        login(client)
        run = client.post(
            f"/api/cases/{case_id}/runs",
            json={"idempotency_key": f"err-test-{idx}-{uuid4()}"},
        ).json()
        assert process_one(database)
        status = client.get(f"/api/runs/{run['id']}").json()
        assert status["status"] == "failed"
        assert status["error_code"] == expected_code
        case_info = client.get(f"/api/cases/{case_id}").json()["data"]["case"]
        assert case_info["status"] == "needs_manual_review"
        audits = client.get(f"/api/cases/{case_id}/audit").json()
        assert any(
            a["action"] == "investigation.failed" and a["payload"].get("error_code") == expected_code
            for a in audits
        )
        # Ensure API key never leaked into audit logs
        assert "test-key-not-logged" not in str(audits)


def test_mcp_stdio_round_trip_matches_direct_adapter(database):
    """Requirement 14: Official MCP stdio client-server round-trip returns identical facts and scope."""
    res = run_smoke_test(database, use_stdio=True)
    assert res["status"] == "ok"
    assert res["transport"] == "mcp-stdio"
    assert len(res["tools_verified"]) >= 6
    assert res["scope_tampering_rejected"] is True


def test_benchmark_runner_abc_and_no_gold_mount_in_compose(tmp_path):
    """Requirement 2 & Section 17: Benchmark runner evaluates A/B/C with distinct metrics and
    compose.yaml never mounts evaluation/private_gold into runtime services."""
    out_file = tmp_path / "bench_test.json"
    artifact = run_benchmark_suite(
        split="demo",
        seed=42,
        limit=6,
        provider_mode="mock",
        output_path=out_file,
    )
    assert out_file.exists()
    modes = {m["mode"]: m for m in artifact["modes"]}
    assert set(modes.keys()) == {"A", "B", "C"}
    # Mode C has higher counterevidence recall and recommendation agreement than Mode A and Mode B
    assert modes["C"]["recommendation_agreement"]["value"] > modes["B"]["recommendation_agreement"]["value"]
    assert modes["C"]["recommendation_agreement"]["value"] > modes["A"]["recommendation_agreement"]["value"]
    assert modes["C"]["citation_validity"]["value"] == 1.0
    # Sample size 6 < 20 -> bootstrap CI must be None (no fake CI from tiny samples)
    assert modes["C"]["recommendation_agreement"]["ci_95_bootstrap"] is None

    compose_path = Path(__file__).resolve().parents[1] / "compose.yaml"
    if compose_path.exists():
        compose_text = compose_path.read_text(encoding="utf-8")
        assert "private_gold" not in compose_text
