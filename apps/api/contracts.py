from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AllowedToolName = Literal[
    "get_case_context",
    "get_trip_history",
    "get_device_usage_timeline",
    "get_relationship_subgraph",
    "get_promo_ledger",
    "search_policy",
    "get_source_record",
    "STOP",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrustedContext(Contract):
    actor_id: str
    username: str
    role: Literal["investigator", "reviewer", "auditor"]
    tenant_id: str
    case_id: str
    snapshot_id: str
    cutoff: str
    run_id: str | None = None


class LoginInput(Contract):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class StartInput(Contract):
    idempotency_key: str = Field(min_length=8, max_length=100)
    workflow_mode: Literal["B", "C"] = "C"
    transport: Literal["direct", "mcp"] = "direct"
    parent_run_id: str | None = Field(default=None, max_length=64)


class ReviewInput(Contract):
    idempotency_key: str = Field(min_length=8, max_length=100)
    expected_version: int = Field(ge=1)
    report_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    snapshot_id: str
    disposition: Literal[
        "CLOSE_NO_ACTION", "REQUEST_MORE_INFORMATION", "ESCALATE_FOR_MANUAL_REVIEW"
    ]
    rationale: str = Field(min_length=10, max_length=3000)

    @field_validator("rationale")
    @classmethod
    def meaningful_rationale(cls, value: str) -> str:
        if len(value.strip()) < 10:
            raise ValueError("Cần lý do cụ thể, ít nhất 10 ký tự")
        return value.strip()


class GetCaseContextArgs(Contract):
    case_id: str = Field(min_length=1, max_length=64)


class GetTripHistoryArgs(Contract):
    entity_ids: list[str] = Field(default_factory=list, max_length=50)
    start_at: str | None = None
    end_at: str | None = None
    cursor: str | None = None
    page_size: int = Field(default=20, ge=1, le=50)


class GetDeviceUsageArgs(Contract):
    entity_ids: list[str] = Field(default_factory=list, max_length=50)
    start_at: str | None = None
    end_at: str | None = None


class GetSubgraphArgs(Contract):
    entity_ids: list[str] = Field(default_factory=list, max_length=50)
    start_at: str | None = None
    end_at: str | None = None
    max_hops: int = Field(default=2, ge=1, le=3)


class GetPromoLedgerArgs(Contract):
    entity_ids: list[str] = Field(default_factory=list, max_length=50)
    start_at: str | None = None
    end_at: str | None = None


class SearchPolicyArgs(Contract):
    query: str = Field(min_length=1, max_length=300)
    effective_at: str | None = None
    top_k: int = Field(default=4, ge=1, le=5)


class GetSourceRecordArgs(Contract):
    source_ref: str = Field(min_length=3, max_length=128)


class NextStepDecision(Contract):
    hypothesis: str = Field(min_length=3, max_length=400)
    question: str = Field(min_length=3, max_length=400)
    tool_name: AllowedToolName
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(min_length=3, max_length=500)
    stop_condition: str = Field(min_length=3, max_length=300)


class UsageProvenance(Contract):
    input_units: int = 0
    output_units: int = 0
    total_units: int = 0
    usage_source: Literal["not_applicable", "provider_reported", "estimated"] = "not_applicable"
    estimated_cost_usd: float | None = None
    pricing_as_of: str | None = None
    llm_calls: int = 0
    max_llm_calls: int = 3
    tool_calls: int = 0
    max_tool_calls: int = 6
    repair_attempts: int = 0
    max_repairs: int = 1


class Claim(Contract):
    claim_id: str
    kind: Literal["OBSERVED", "INFERRED"]
    text: str
    evidence_ids: list[str] = Field(min_length=1)


class ReportContent(Contract):
    summary: str
    hypotheses: list[str]
    supporting_claims: list[Claim]
    counterevidence_claims: list[Claim]
    missing_information: list[Claim]
    next_steps: list[str]
    planned_steps: list[NextStepDecision] = Field(default_factory=list)
    recommendation: Literal[
        "ESCALATE_FOR_REVIEW", "NO_ACTION_SUGGESTED", "INSUFFICIENT_EVIDENCE"
    ]
    readiness_state: Literal[
        "ready_for_review", "missing_evidence", "source_error", "validation_failed"
    ] = "ready_for_review"
    policy_refs: list[str]
    facts: dict[str, int]
    validation_findings: list[str]
    workflow_mode: Literal["A", "B", "C"] = "C"
    provider_mode: Literal["mock", "live", "degraded", "none"] = "mock"
    model_id: str = "deterministic-template-v1"
    prompt_version: str = "tracelens-v2"
    retrieval_mode: Literal["lexical", "hybrid"] = "lexical"
    transport: Literal["direct", "mcp"] = "direct"
    usage: UsageProvenance = Field(default_factory=UsageProvenance)
    timezone: str = "UTC; display Asia/Ho_Chi_Minh"


class ToolEnvelope(Contract):
    request_id: str
    source_system: str
    source_record_ids: list[str]
    as_of: str
    snapshot_id: str
    completeness: Literal["complete", "partial", "unavailable"]
    freshness: str
    truncated: bool = False
    next_cursor: str | None = None
    data: dict
    error: str | None = None
