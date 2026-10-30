from fastapi import HTTPException

from apps.api.contracts import (
    Claim,
    NextStepDecision,
    ReportContent,
    ToolEnvelope,
    UsageProvenance,
)
from apps.api.domain import get_source
from apps.api.models import Evidence, uid, utcnow


def build_report(
    case,
    run,
    envelope: ToolEnvelope,
    workflow_mode: str = "C",
    provider_mode: str = "mock",
    model_id: str = "deterministic-template-v1",
    retrieval_mode: str = "lexical",
    transport: str = "direct",
    planned_steps: list[NextStepDecision] | None = None,
    usage: UsageProvenance | None = None,
):
    facts = envelope.data["facts"]
    evidence = []
    for category, prefixes, values in [
        (
            "activity",
            ("trip:", "rider:", "device:", "usage:", "session:"),
            {k: v for k, v in facts.items() if k not in {"redemption_vnd", "reversal_vnd", "net_vnd"}},
        ),
        (
            "ledger",
            ("ledger:",),
            {k: facts[k] for k in ["redemption_vnd", "reversal_vnd", "net_vnd"] if k in facts},
        ),
        ("quality", ("quality:",), {"completeness": envelope.completeness, "error": envelope.error}),
    ]:
        evidence.append(
            Evidence(
                id=uid(),
                case_id=case.id,
                run_id=run.id,
                tenant_id=case.tenant_id,
                snapshot_id=case.snapshot_id,
                kind="DATA_QUALITY" if category == "quality" else "FACT",
                source_refs=[ref for ref in envelope.source_record_ids if ref.startswith(prefixes)],
                observed_at=case.cutoff,
                retrieved_at=utcnow(),
                structured_values=values,
                calculation_ref=f"facts-v1/{category}",
                completeness=envelope.completeness,
                freshness=envelope.freshness,
            )
        )
    activity, ledger, quality = [e.id for e in evidence]
    supporting = [
        Claim(
            claim_id="shared",
            kind="OBSERVED",
            text="Các tài khoản có liên kết thiết bị; đây là tín hiệu cần đối chiếu, chưa phải kết luận.",
            evidence_ids=[activity],
        )
    ]
    counter: list[Claim] = []
    missing: list[Claim] = []
    recommendation = "INSUFFICIENT_EVIDENCE"
    readiness_state = "ready_for_review"

    if envelope.completeness != "complete" or not envelope.data["policies"]:
        readiness_state = "missing_evidence"
        missing.append(
            Claim(
                claim_id="missing",
                kind="OBSERVED",
                text="Nguồn chưa được thu thập đầy đủ, bị lỗi hoặc hết ngân sách. Không suy diễn thiếu dữ liệu thành không có bằng chứng.",
                evidence_ids=[quality],
            )
        )
    elif workflow_mode == "B":
        # Mode B (Fixed workflow): runs fixed shallow checklist without targeted device-interval
        # counterevidence verification. Only ledger reversals are observed directly.
        if facts["reversal_vnd"] < 0:
            counter.append(
                Claim(
                    claim_id="reversal",
                    kind="OBSERVED",
                    text="Sổ có bút toán đảo làm giảm ưu đãi ròng so với tổng redemption.",
                    evidence_ids=[ledger],
                )
            )
        if envelope.completeness != "complete" or not envelope.data["policies"]:
            readiness_state = "source_error" if envelope.error == "SOURCE_UNAVAILABLE" else "missing_evidence"
            missing.append(
                Claim(
                    claim_id="missing",
                    kind="OBSERVED",
                    text="Nguồn hoặc chính sách chưa đầy đủ; không thể coi thiếu dữ liệu là bằng chứng vắng mặt.",
                    evidence_ids=[quality],
                )
            )
        elif facts["net_vnd"] == 0 and facts["reversal_vnd"] < 0:
            recommendation = "NO_ACTION_SUGGESTED"
        elif (
            facts["new_accounts"] >= 3
            and facts["trip_span_minutes"] <= 60
            and facts["driver_count"] == 1
            and facts["net_vnd"] > 0
        ):
            recommendation = "ESCALATE_FOR_REVIEW"
            supporting.append(
                Claim(
                    claim_id="burst",
                    kind="INFERRED",
                    text="Các chuyến đi tập trung trong thời gian ngắn với tài khoản mới và ưu đãi ròng dương.",
                    evidence_ids=[activity, ledger],
                )
            )
        else:
            readiness_state = "missing_evidence"
            missing.append(
                Claim(
                    claim_id="unverified_intervals",
                    kind="OBSERVED",
                    text="Checklist cố định (Mode B) chưa thực hiện bước kiểm tra chuyên sâu khoảng chồng lấn thiết bị và subgraph.",
                    evidence_ids=[activity, quality],
                )
            )
    else:
        # Mode C (Evidence-driven adaptive workflow): inspects targeted counterevidence
        if facts["overlap_pairs"] > 0:
            supporting.append(
                Claim(
                    claim_id="overlap",
                    kind="OBSERVED",
                    text="Khoảng sử dụng thiết bị giữa các tài khoản có chồng lấn.",
                    evidence_ids=[activity],
                )
            )
        else:
            counter.append(
                Claim(
                    claim_id="sequential",
                    kind="OBSERVED",
                    text="Các khoảng sử dụng thiết bị không chồng lấn; phù hợp khả năng dùng kế tiếp.",
                    evidence_ids=[activity],
                )
            )
        if facts["new_accounts"] == 0:
            counter.append(
                Claim(
                    claim_id="history",
                    kind="OBSERVED",
                    text="Các tài khoản đã có lịch sử trước cửa sổ tài khoản mới.",
                    evidence_ids=[activity],
                )
            )
        if facts["reversal_vnd"] < 0:
            counter.append(
                Claim(
                    claim_id="reversal",
                    kind="OBSERVED",
                    text="Sổ có bút toán đảo làm giảm ưu đãi ròng so với tổng redemption.",
                    evidence_ids=[ledger],
                )
            )
        if envelope.completeness != "complete" or not envelope.data["policies"]:
            readiness_state = "source_error" if envelope.error == "SOURCE_UNAVAILABLE" else "missing_evidence"
            missing.append(
                Claim(
                    claim_id="missing",
                    kind="OBSERVED",
                    text="Nguồn hoặc chính sách chưa đầy đủ; không thể coi thiếu dữ liệu là bằng chứng vắng mặt.",
                    evidence_ids=[quality],
                )
            )
        elif (
            facts["new_accounts"] >= 3
            and facts["overlap_pairs"] >= 2
            and facts["trip_span_minutes"] <= 60
            and facts["driver_count"] == 1
            and facts["net_vnd"] > 0
        ):
            recommendation = "ESCALATE_FOR_REVIEW"
            supporting.append(
                Claim(
                    claim_id="combined",
                    kind="INFERRED",
                    text="Tài khoản mới, hoạt động tập trung và khoảng sử dụng chồng lấn tạo thành nhiều tín hiệu "
                    "cần người có quyền kiểm tra thêm. Chưa xác nhận gian lận.",
                    evidence_ids=[activity, ledger],
                )
            )
        elif facts["overlap_pairs"] == 0 and facts["new_accounts"] == 0 and facts["trip_span_minutes"] > 1440:
            recommendation = "NO_ACTION_SUGGESTED"
        else:
            readiness_state = "missing_evidence"
            missing.append(
                Claim(
                    claim_id="context",
                    kind="INFERRED",
                    text="Các tín hiệu hiện tại chưa đủ phân biệt phối hợp với cách sử dụng hợp lệ.",
                    evidence_ids=[activity, quality],
                )
            )

    mode_label = (
        "Mock workflow — chưa đo năng lực LLM thật."
        if provider_mode == "mock"
        else f"Provider mode: {provider_mode} ({model_id})."
    )
    content = ReportContent(
        summary=f"Hồ sơ tổng hợp (Mode {workflow_mode}) từ bản chụp dữ liệu. {mode_label}",
        hypotheses=[
            "Có thể có phối hợp sử dụng ưu đãi",
            "Có thể là dùng chung hoặc chuyển giao thiết bị hợp lệ",
            "Có thể là trùng mạng văn phòng, đi làm định kỳ hoặc đã hoàn trả voucher",
        ],
        supporting_claims=supporting,
        counterevidence_claims=counter,
        missing_information=missing,
        next_steps=["Người duyệt mở nguồn, đối chiếu phản bác và ghi lý do trước khi quyết định sandbox."],
        planned_steps=planned_steps or [],
        recommendation=recommendation,
        readiness_state=readiness_state,
        policy_refs=[f"policy:{p['id']}" for p in envelope.data["policies"]],
        facts=dict(facts),
        validation_findings=[],
        workflow_mode=workflow_mode if workflow_mode in {"A", "B", "C"} else "C",
        provider_mode=provider_mode if provider_mode in {"mock", "live", "degraded", "none"} else "mock",
        model_id=model_id,
        retrieval_mode=retrieval_mode if retrieval_mode in {"lexical", "hybrid"} else "lexical",
        transport=transport if transport in {"direct", "mcp"} else "direct",
        usage=usage or UsageProvenance(),
    )
    return content, evidence


def validate_report(
    db, actor, case, run, content: ReportContent, evidence: list[Evidence], expected_facts: dict
):
    findings = []
    evidence_by_id = {item.id: item for item in evidence}
    for claim in content.supporting_claims + content.counterevidence_claims + content.missing_information:
        for eid in claim.evidence_ids:
            item = evidence_by_id.get(eid)
            if not item or (item.case_id, item.tenant_id, item.snapshot_id, item.run_id) != (
                case.id,
                case.tenant_id,
                case.snapshot_id,
                run.id,
            ):
                findings.append("EVIDENCE_SCOPE_MISMATCH")
    for item in evidence:
        for ref in item.source_refs:
            try:
                get_source(db, actor, case.id, ref)
            except HTTPException:
                findings.append("SOURCE_SCOPE_OR_VALIDITY_ERROR")
    for ref in content.policy_refs:
        try:
            get_source(db, actor, case.id, ref)
        except HTTPException:
            findings.append("POLICY_SCOPE_OR_VALIDITY_ERROR")
    if content.facts != expected_facts:
        findings.append("FACTS_MISMATCH")
    for item in evidence:
        if item.kind == "FACT" and any(expected_facts.get(k) != v for k, v in item.structured_values.items()):
            findings.append("EVIDENCE_VALUES_MISMATCH")
    return sorted(set(findings))


def repair_report(
    db,
    actor,
    case,
    run,
    content: ReportContent,
    evidence: list[Evidence],
    expected_facts: dict,
    findings: list[str],
) -> tuple[ReportContent, list[str]]:
    """Bounded single-step deterministic repair for recoverable report validation issues."""
    content.usage.repair_attempts += 1
    if "FACTS_MISMATCH" in findings:
        content.facts = dict(expected_facts)
    if "EVIDENCE_VALUES_MISMATCH" in findings:
        for item in evidence:
            if item.kind == "FACT":
                for k in list(item.structured_values.keys()):
                    if k in expected_facts:
                        item.structured_values[k] = expected_facts[k]
    if "POLICY_SCOPE_OR_VALIDITY_ERROR" in findings:
        valid_policies = []
        for ref in content.policy_refs:
            try:
                get_source(db, actor, case.id, ref)
                valid_policies.append(ref)
            except HTTPException:
                pass
        content.policy_refs = valid_policies

    remaining = validate_report(db, actor, case, run, content, evidence, expected_facts)
    content.validation_findings = remaining
    if remaining:
        content.readiness_state = "validation_failed"
    return content, remaining
