"""Rubric v1 for offline synthetic evaluation of TraceLens modes A, B, and C.

Strictly defines denominators for every metric:
- Evidence recall: matched required supporting signals / total required supporting signals in gold.
- Counterevidence recall: matched required counterevidence signals / total required counterevidence
  signals in gold. Cases where gold has 0 required counterevidence signals are `not_applicable`
  (excluded from the denominator, never counted as 0 or 100).
- Citation validity: accessible, in-scope, snapshot-matching citations / total citations in report.
- Unsupported claim rate: claims whose cited evidence structured values do not support the claim /
  total evaluated claims.
- Recommendation agreement: 3x3 confusion matrix against synthetic rubric (`ESCALATE_FOR_REVIEW`,
  `NO_ACTION_SUGGESTED`, `INSUFFICIENT_EVIDENCE`), never labeled as real-world fraud accuracy.
- Bootstrap 95% CI: computed only when sample_size >= 20; returns None for small samples.
"""

import random
from typing import Any

from fastapi import HTTPException

from apps.api.domain import get_source

RECOMMENDATION_LABELS = [
    "ESCALATE_FOR_REVIEW",
    "NO_ACTION_SUGGESTED",
    "INSUFFICIENT_EVIDENCE",
]


def _signal_matched_in_claims(signal: str, claims: list[dict[str, Any]], facts: dict[str, Any]) -> bool:
    claim_ids = {c.get("claim_id", "") for c in claims}
    combined_text = " ".join(c.get("text", "").lower() for c in claims)

    if signal in {"shared_device_or_alert_signal", "shared_device_signal"}:
        return "shared" in claim_ids or "liên kết thiết bị" in combined_text
    if signal == "overlapping_device_intervals":
        return ("overlap" in claim_ids or "chồng lấn" in combined_text) and facts.get("overlap_pairs", 0) > 0
    if signal == "concentrated_new_accounts":
        return (
            "combined" in claim_ids
            or "burst" in claim_ids
            or "tài khoản mới" in combined_text
        )
    if signal == "sequential_non_overlapping_usage":
        return "sequential" in claim_ids or "không chồng lấn" in combined_text or "kế tiếp" in combined_text
    if signal == "established_account_history":
        return "history" in claim_ids or "đã có lịch sử" in combined_text
    if signal == "promo_reversal_reduces_net":
        return "reversal" in claim_ids or "bút toán đảo" in combined_text
    if signal == "source_incomplete_or_ambiguous_context":
        return bool({"missing", "context", "unverified_intervals"} & claim_ids)
    return False


def evaluate_case_report(
    db,
    actor,
    case,
    report_payload: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
    gold_entry: dict[str, Any],
) -> dict[str, Any]:
    supporting = report_payload.get("supporting_claims", [])
    counter = report_payload.get("counterevidence_claims", [])
    missing = report_payload.get("missing_information", [])
    all_claims = supporting + counter + missing
    facts = report_payload.get("facts", {})

    # 1. Supporting evidence recall
    req_sup = gold_entry.get("required_supporting_signals", [])
    matched_sup = sum(
        1 for sig in req_sup if _signal_matched_in_claims(sig, supporting + missing, facts)
    )
    evidence_recall = (matched_sup / len(req_sup)) if req_sup else None

    # 2. Counterevidence recall (None when not applicable)
    req_cnt = gold_entry.get("required_counterevidence_signals", [])
    if req_cnt:
        matched_cnt = sum(1 for sig in req_cnt if _signal_matched_in_claims(sig, counter, facts))
        counter_recall: float | None = matched_cnt / len(req_cnt)
    else:
        matched_cnt = 0
        counter_recall = None

    # 3. Citation validity (deterministic scope/snapshot/existence check)
    ev_by_id = {e["id"]: e for e in evidence_rows}
    total_citations = 0
    valid_citations = 0

    for c in all_claims:
        for eid in c.get("evidence_ids", []):
            total_citations += 1
            ev = ev_by_id.get(eid)
            if (
                ev
                and ev.get("case_id") == case.id
                and ev.get("tenant_id") == case.tenant_id
                and ev.get("snapshot_id") == case.snapshot_id
            ):
                all_refs_ok = bool(ev.get("source_refs"))
                for ref in ev.get("source_refs", []):
                    try:
                        get_source(db, actor, case.id, ref)
                    except HTTPException:
                        all_refs_ok = False
                        break
                if all_refs_ok:
                    valid_citations += 1

    for pref in report_payload.get("policy_refs", []):
        total_citations += 1
        try:
            get_source(db, actor, case.id, pref)
            valid_citations += 1
        except HTTPException:
            pass

    citation_validity = (valid_citations / total_citations) if total_citations > 0 else 0.0

    # 4. Unsupported claim rate (rubric check against structured evidence values)
    unsupported_claims = 0
    for c in all_claims:
        cid = c.get("claim_id", "")
        eids = c.get("evidence_ids", [])
        if not eids or any(eid not in ev_by_id for eid in eids):
            unsupported_claims += 1
            continue
        if cid == "overlap" and facts.get("overlap_pairs", 0) <= 0:
            unsupported_claims += 1
        elif cid == "sequential" and facts.get("overlap_pairs", 0) > 0:
            unsupported_claims += 1
        elif cid == "reversal" and facts.get("reversal_vnd", 0) >= 0:
            unsupported_claims += 1
        elif cid == "history" and facts.get("new_accounts", 0) > 0:
            unsupported_claims += 1

    unsupported_rate = (unsupported_claims / len(all_claims)) if all_claims else 0.0

    # 5. Recommendation agreement
    actual_rec = report_payload.get("recommendation", "INSUFFICIENT_EVIDENCE")
    expected_rec = gold_entry["expected_recommendation"]
    rec_match = actual_rec == expected_rec

    return {
        "case_id": case.id,
        "scenario_family": gold_entry["scenario_family"],
        "expected_recommendation": expected_rec,
        "actual_recommendation": actual_rec,
        "recommendation_match": rec_match,
        "supporting_matched": matched_sup,
        "supporting_required": len(req_sup),
        "evidence_recall": evidence_recall,
        "counter_matched": matched_cnt,
        "counter_required": len(req_cnt),
        "counterevidence_recall": counter_recall,
        "valid_citations": valid_citations,
        "total_citations": total_citations,
        "citation_validity": citation_validity,
        "unsupported_claims": unsupported_claims,
        "total_claims": len(all_claims),
        "unsupported_claim_rate": unsupported_rate,
    }


def bootstrap_ci_95(values: list[float], seed: int = 42, n_boot: int = 500) -> list[float] | None:
    """Computes 95% non-parametric bootstrap CI only when sample size >= 20."""
    if len(values) < 20:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_boot):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot)]
    return [round(lo, 4), round(hi, 4)]


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round((len(s) - 1) * q))))
    return round(s[idx], 2)


def aggregate_mode_metrics(
    mode: str,
    case_evals: list[dict[str, Any]],
    latencies_ms: list[float],
    tool_counts: list[int],
    llm_counts: list[int],
    timeout_count: int = 0,
    failure_count: int = 0,
    seed: int = 42,
) -> dict[str, Any]:
    sup_matched = sum(c["supporting_matched"] for c in case_evals)
    sup_req = sum(c["supporting_required"] for c in case_evals)
    cnt_applicable = [c for c in case_evals if c["counter_required"] > 0]
    cnt_matched = sum(c["counter_matched"] for c in cnt_applicable)
    cnt_req = sum(c["counter_required"] for c in cnt_applicable)

    cit_valid = sum(c["valid_citations"] for c in case_evals)
    cit_total = sum(c["total_citations"] for c in case_evals)

    unsup_count = sum(c["unsupported_claims"] for c in case_evals)
    claims_total = sum(c["total_claims"] for c in case_evals)

    rec_matches = [1.0 if c["recommendation_match"] else 0.0 for c in case_evals]
    insuff_cases = [c for c in case_evals if c["expected_recommendation"] == "INSUFFICIENT_EVIDENCE"]
    insuff_matches = sum(1 for c in insuff_cases if c["recommendation_match"])

    confusion: dict[str, dict[str, int]] = {
        exp: {act: 0 for act in RECOMMENDATION_LABELS} for exp in RECOMMENDATION_LABELS
    }
    for c in case_evals:
        exp = c["expected_recommendation"]
        act = c["actual_recommendation"]
        if exp in confusion and act in confusion[exp]:
            confusion[exp][act] += 1

    return {
        "mode": mode,
        "sample_size": len(case_evals),
        "attempted_cases": len(case_evals) + failure_count,
        "failure_count": failure_count,
        "timeout_count": timeout_count,
        "evidence_recall": {
            "value": round(sup_matched / sup_req, 4) if sup_req else 0.0,
            "numerator": sup_matched,
            "denominator": sup_req,
        },
        "counterevidence_recall": {
            "value": round(cnt_matched / cnt_req, 4) if cnt_req else None,
            "numerator": cnt_matched,
            "denominator": cnt_req,
            "applicable_cases": len(cnt_applicable),
            "not_applicable_cases": len(case_evals) - len(cnt_applicable),
        },
        "citation_validity": {
            "value": round(cit_valid / cit_total, 4) if cit_total else 0.0,
            "numerator": cit_valid,
            "denominator": cit_total,
        },
        "unsupported_claim_rate": {
            "value": round(unsup_count / claims_total, 4) if claims_total else 0.0,
            "numerator": unsup_count,
            "denominator": claims_total,
        },
        "recommendation_agreement": {
            "value": round(sum(rec_matches) / len(rec_matches), 4) if rec_matches else 0.0,
            "numerator": int(sum(rec_matches)),
            "denominator": len(rec_matches),
            "ci_95_bootstrap": bootstrap_ci_95(rec_matches, seed=seed),
            "insufficient_evidence_subset": {
                "matched": insuff_matches,
                "total": len(insuff_cases),
            },
            "confusion_matrix": confusion,
        },
        "efficiency": {
            "mean_tool_calls": round(sum(tool_counts) / len(tool_counts), 2) if tool_counts else 0.0,
            "max_tool_calls": max(tool_counts, default=0),
            "mean_llm_calls": round(sum(llm_counts) / len(llm_counts), 2) if llm_counts else 0.0,
            "latency_p50_ms": percentile(latencies_ms, 0.50),
            "latency_p95_ms": percentile(latencies_ms, 0.95),
        },
    }

