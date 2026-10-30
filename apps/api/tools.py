"""Typed read-only tool registry, scope token signing, and direct tool dispatcher.

No write tool, arbitrary SQL, Cypher, shell, filesystem, or URL fetch tool is ever
registered in `READONLY_TOOL_ALLOWLIST`.
"""

import base64
import hashlib
import hmac
import json
from typing import Any

from pydantic import ValidationError

from apps.api.config import settings
from apps.api.contracts import (
    GetCaseContextArgs,
    GetDeviceUsageArgs,
    GetPromoLedgerArgs,
    GetSourceRecordArgs,
    GetSubgraphArgs,
    GetTripHistoryArgs,
    SearchPolicyArgs,
    ToolEnvelope,
    TrustedContext,
)
from apps.api.domain import (
    readonly_session,
    require_case,
    tool_get_case_context,
    tool_get_device_usage_timeline,
    tool_get_promo_ledger,
    tool_get_relationship_subgraph,
    tool_get_source_record,
    tool_get_trip_history,
    tool_search_policy,
)
from apps.api.models import User, uid

TOOL_SPECIFICATIONS: dict[str, dict[str, Any]] = {
    "get_case_context": {
        "schema": GetCaseContextArgs,
        "timeout_seconds": 5.0,
        "max_records": 100,
        "description": (
            "Retrieve alert metadata, scoped entity IDs, account ages, and source quality status. "
            "USE THIS WHEN: starting an investigation or checking overall case scope and alert features. "
            "DO NOT USE WHEN: calculating voucher net amounts or inspecting detailed trip/device intervals."
        ),
    },
    "get_trip_history": {
        "schema": GetTripHistoryArgs,
        "timeout_seconds": 5.0,
        "max_records": 50,
        "description": (
            "Retrieve paginated trip history, driver count, and trip time span within cutoff. "
            "USE THIS WHEN: checking whether trips occurred in a short burst with one driver or across many days. "
            "DO NOT USE WHEN: computing promo reversals or device interval overlap."
        ),
    },
    "get_device_usage_timeline": {
        "schema": GetDeviceUsageArgs,
        "timeout_seconds": 5.0,
        "max_records": 100,
        "description": (
            "Retrieve device usage intervals, session notes, IP prefixes, and overlap_pairs count. "
            "USE THIS WHEN: testing whether device sharing was simultaneous (overlapping) or sequential handoff/household. "
            "DO NOT USE WHEN: querying SOP documents or voucher ledger balances."
        ),
    },
    "get_relationship_subgraph": {
        "schema": GetSubgraphArgs,
        "timeout_seconds": 5.0,
        "max_records": 150,
        "description": (
            "Retrieve bounded multi-hop entity relationship subgraph (max_hops <= 3, max 50 nodes / 100 edges). "
            "USE THIS WHEN: inspecting shared connections across riders, devices, drivers, and IP networks. "
            "DO NOT USE WHEN: treating graph connectivity as proof of fraud."
        ),
    },
    "get_promo_ledger": {
        "schema": GetPromoLedgerArgs,
        "timeout_seconds": 5.0,
        "max_records": 200,
        "description": (
            "Retrieve voucher redemptions, reversal entries, and code-calculated net VND totals within cutoff. "
            "USE THIS WHEN: verifying net promo exposure and checking if redemptions were reversed/refunded. "
            "DO NOT USE WHEN: doing mental/free-text arithmetic on monetary values."
        ),
    },
    "search_policy": {
        "schema": SearchPolicyArgs,
        "timeout_seconds": 5.0,
        "max_records": 5,
        "description": (
            "Search versioned SOP and policy sections filtered by tenant, role ACL, and effective timestamp. "
            "USE THIS WHEN: looking up official rules for shared devices, reversals, evidence sufficiency, or escalation. "
            "DO NOT USE WHEN: querying transactional rows or rider/trip data."
        ),
    },
    "get_source_record": {
        "schema": GetSourceRecordArgs,
        "timeout_seconds": 5.0,
        "max_records": 1,
        "description": (
            "Fetch a single authorized internal source record or policy section by source_ref. "
            "USE THIS WHEN: verifying an exact citation record inside the current case scope and snapshot. "
            "DO NOT USE WHEN: attempting to read outside the case scope or fetch external URLs."
        ),
    },
}

READONLY_TOOL_ALLOWLIST: frozenset[str] = frozenset(TOOL_SPECIFICATIONS.keys())


def issue_scope_token(ctx: TrustedContext) -> str:
    payload_bytes = json.dumps(
        ctx.model_dump(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii")
    sig = hmac.new(
        settings.mcp_signing_secret.encode("utf-8"), b64.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{b64}.{sig}"


def verify_scope_token(token: str) -> TrustedContext:
    b64, dot, sig = token.partition(".")
    if not dot or not b64 or not sig:
        raise ValueError("INVALID_SCOPE_TOKEN")
    expected_sig = hmac.new(
        settings.mcp_signing_secret.encode("utf-8"), b64.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("INVALID_SCOPE_TOKEN_SIGNATURE")
    try:
        raw = json.loads(base64.urlsafe_b64decode(b64.encode("ascii")).decode("utf-8"))
        return TrustedContext.model_validate(raw)
    except Exception as exc:
        raise ValueError("INVALID_SCOPE_TOKEN_PAYLOAD") from exc


def execute_direct_tool(
    database_engine,
    ctx: TrustedContext,
    tool_name: str,
    raw_args: dict[str, Any] | None = None,
) -> ToolEnvelope:
    raw_args = raw_args or {}
    if tool_name not in READONLY_TOOL_ALLOWLIST:
        return ToolEnvelope(
            request_id=uid(),
            source_system="tool-guard",
            source_record_ids=[],
            as_of=ctx.cutoff,
            snapshot_id=ctx.snapshot_id,
            completeness="unavailable",
            freshness="fixed_snapshot",
            data={},
            error="TOOL_NOT_ALLOWED",
        )

    # Reject any attempt to tamper with trusted scope keys in tool arguments
    forbidden_arg_keys = {"tenant_id", "actor_id", "role", "snapshot_id", "cutoff", "run_id"}
    if any(k in raw_args for k in forbidden_arg_keys):
        return ToolEnvelope(
            request_id=uid(),
            source_system="tool-guard",
            source_record_ids=[],
            as_of=ctx.cutoff,
            snapshot_id=ctx.snapshot_id,
            completeness="unavailable",
            freshness="fixed_snapshot",
            data={},
            error="SCOPE_TAMPERING_REJECTED",
        )

    spec = TOOL_SPECIFICATIONS[tool_name]
    schema_cls = spec["schema"]
    # For get_case_context, default case_id to ctx.case_id if omitted, but reject if mismatched
    if tool_name == "get_case_context":
        supplied_case = raw_args.get("case_id", ctx.case_id)
        if supplied_case != ctx.case_id:
            return ToolEnvelope(
                request_id=uid(),
                source_system="tool-guard",
                source_record_ids=[],
                as_of=ctx.cutoff,
                snapshot_id=ctx.snapshot_id,
                completeness="unavailable",
                freshness="fixed_snapshot",
                data={},
                error="SCOPE_VIOLATION",
            )
        raw_args = {"case_id": ctx.case_id}

    try:
        validated = schema_cls.model_validate(raw_args)
    except ValidationError as exc:
        return ToolEnvelope(
            request_id=uid(),
            source_system="tool-guard",
            source_record_ids=[],
            as_of=ctx.cutoff,
            snapshot_id=ctx.snapshot_id,
            completeness="unavailable",
            freshness="fixed_snapshot",
            data={"validation_errors": exc.errors()},
            error="INVALID_TOOL_ARGUMENTS",
        )

    with readonly_session(database_engine) as reader:
        # Always re-verify actor and case scope against server DB state
        actor = reader.get(User, ctx.actor_id)
        if (
            not actor
            or not actor.active
            or actor.tenant_id != ctx.tenant_id
            or actor.role != ctx.role
        ):
            return ToolEnvelope(
                request_id=uid(),
                source_system="tool-guard",
                source_record_ids=[],
                as_of=ctx.cutoff,
                snapshot_id=ctx.snapshot_id,
                completeness="unavailable",
                freshness="fixed_snapshot",
                data={},
                error="ACTOR_UNAUTHORIZED",
            )
        try:
            case = require_case(reader, actor, ctx.case_id)
        except Exception:
            return ToolEnvelope(
                request_id=uid(),
                source_system="tool-guard",
                source_record_ids=[],
                as_of=ctx.cutoff,
                snapshot_id=ctx.snapshot_id,
                completeness="unavailable",
                freshness="fixed_snapshot",
                data={},
                error="CASE_OUT_OF_SCOPE",
            )
        if case.snapshot_id != ctx.snapshot_id:
            return ToolEnvelope(
                request_id=uid(),
                source_system="tool-guard",
                source_record_ids=[],
                as_of=ctx.cutoff,
                snapshot_id=ctx.snapshot_id,
                completeness="unavailable",
                freshness="fixed_snapshot",
                data={},
                error="SNAPSHOT_CHANGED",
            )

        if tool_name == "get_case_context":
            return tool_get_case_context(reader, actor, case.id)
        if tool_name == "get_trip_history":
            return tool_get_trip_history(
                reader,
                actor,
                case.id,
                entity_ids=validated.entity_ids,
                start_at=validated.start_at,
                end_at=validated.end_at,
                cursor=validated.cursor,
                page_size=validated.page_size,
            )
        if tool_name == "get_device_usage_timeline":
            return tool_get_device_usage_timeline(
                reader,
                actor,
                case.id,
                entity_ids=validated.entity_ids,
                start_at=validated.start_at,
                end_at=validated.end_at,
            )
        if tool_name == "get_relationship_subgraph":
            return tool_get_relationship_subgraph(
                reader,
                actor,
                case.id,
                entity_ids=validated.entity_ids,
                start_at=validated.start_at,
                end_at=validated.end_at,
                max_hops=validated.max_hops,
            )
        if tool_name == "get_promo_ledger":
            return tool_get_promo_ledger(
                reader,
                actor,
                case.id,
                entity_ids=validated.entity_ids,
                start_at=validated.start_at,
                end_at=validated.end_at,
            )
        if tool_name == "search_policy":
            return tool_search_policy(
                reader,
                actor,
                case.id,
                query=validated.query,
                effective_at=validated.effective_at,
                top_k=validated.top_k,
            )
        if tool_name == "get_source_record":
            return tool_get_source_record(reader, actor, case.id, source_ref=validated.source_ref)

    raise RuntimeError(f"Unhandled tool {tool_name}")

