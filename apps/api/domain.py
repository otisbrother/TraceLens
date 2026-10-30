"""Authorized read services shared by dashboard, direct tools, and MCP adapter.

Only these services produce investigation facts. No SQL, filesystem or arbitrary URL
interface is exposed to a model. Source reads use a database-enforced read-only transaction.
"""

import hashlib
import json
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from itertools import combinations

from fastapi import HTTPException
from sqlalchemy import select, text

from apps.api.contracts import ToolEnvelope
from apps.api.models import (
    Alert,
    Campaign,
    Case,
    Device,
    DeviceUsage,
    Driver,
    LedgerEntry,
    Policy,
    Rider,
    SessionEvent,
    SourceQuality,
    Trip,
    uid,
)
from apps.api.rag import retrieve_policies

SOURCE_MODELS = {
    "rider": Rider,
    "driver": Driver,
    "device": Device,
    "usage": DeviceUsage,
    "session": SessionEvent,
    "trip": Trip,
    "campaign": Campaign,
    "ledger": LedgerEntry,
    "quality": SourceQuality,
}


def serialize(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def digest(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def require_case(db, actor, case_id: str) -> Case:
    if not actor or not actor.active:
        raise HTTPException(404, "Không tìm thấy hồ sơ trong phạm vi được phép")
    case = db.scalar(select(Case).where(Case.id == case_id, Case.tenant_id == actor.tenant_id))
    if not case:
        raise HTTPException(404, "Không tìm thấy hồ sơ trong phạm vi được phép")
    return case


@contextmanager
def readonly_session(engine):
    from sqlalchemy.orm import Session

    with engine.connect() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA query_only=ON")
        else:
            connection.execute(text("SET TRANSACTION READ ONLY"))
        try:
            with Session(bind=connection) as db:
                yield db
        finally:
            connection.rollback()
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("PRAGMA query_only=OFF")
                connection.rollback()


def scoped_rows(db, model, case):
    return list(
        db.scalars(
            select(model)
            .where(
                model.case_id == case.id,
                model.tenant_id == case.tenant_id,
                model.snapshot_id == case.snapshot_id,
            )
            .order_by(model.id)
        )
    )


def search_policy(db, actor, effective_at: str, query: str, top_k: int = 3, mode: str | None = None):
    return retrieve_policies(db, actor, effective_at, query, top_k=top_k, mode=mode)


def get_source(db, actor, case_id: str, ref: str):
    case = require_case(db, actor, case_id)
    kind, _, record_id = ref.partition(":")
    if kind == "policy":
        row = db.get(Policy, record_id)
        if (
            row
            and row.tenant_id == actor.tenant_id
            and actor.role in row.allowed_roles
            and row.effective_from <= case.cutoff
            and (row.effective_to is None or row.effective_to > case.cutoff)
        ):
            return serialize(row)
    elif kind in SOURCE_MODELS:
        model = SOURCE_MODELS[kind]
        row = db.scalar(
            select(model).where(
                model.id == record_id,
                model.case_id == case.id,
                model.tenant_id == actor.tenant_id,
                model.snapshot_id == case.snapshot_id,
            )
        )
        if row:
            timestamp = getattr(row, "occurred_at", getattr(row, "started_at", None))
            if timestamp is None or timestamp <= case.cutoff:
                return serialize(row)
    raise HTTPException(404, "Nguồn không tồn tại hoặc không thuộc phạm vi hồ sơ")


def _check_source_quality(quality_rows: list[SourceQuality], system: str, cutoff: str) -> tuple[str, str | None]:
    matches = [q for q in quality_rows if q.source_system == system]
    if not matches:
        return "partial", "SOURCE_INCOMPLETE"
    q = matches[0]
    if q.completeness == "timeout":
        return "unavailable", "SOURCE_TIMEOUT"
    if q.completeness == "unavailable":
        return "unavailable", "SOURCE_UNAVAILABLE"
    if q.completeness != "complete" or q.as_of < cutoff:
        return "partial", "SOURCE_INCOMPLETE"
    return "complete", None


def _validate_entity_scope(case_id: str, allowed_ids: set[str], requested_ids: list[str]) -> bool:
    if not requested_ids:
        return True
    return all(eid in allowed_ids for eid in requested_ids)


def gather_facts(db, actor, case_id: str, include_policies: bool = True) -> ToolEnvelope:
    case = require_case(db, actor, case_id)
    riders = scoped_rows(db, Rider, case)
    drivers = scoped_rows(db, Driver, case)
    devices = scoped_rows(db, Device, case)
    trips = [r for r in scoped_rows(db, Trip, case) if r.started_at <= case.cutoff]
    usage = [r for r in scoped_rows(db, DeviceUsage, case) if r.start_at <= case.cutoff]
    sessions = [r for r in scoped_rows(db, SessionEvent, case) if r.occurred_at <= case.cutoff]
    ledger = [r for r in scoped_rows(db, LedgerEntry, case) if r.occurred_at <= case.cutoff]
    quality = scoped_rows(db, SourceQuality, case)
    sizes = [len(riders), len(trips), len(usage), len(ledger)]
    truncated = any(n > 500 for n in sizes)
    riders, trips, usage, ledger = riders[:500], trips[:500], usage[:500], ledger[:500]
    overlaps = sum(
        1
        for a, b in combinations(usage, 2)
        if a.device_id == b.device_id
        and a.rider_id != b.rider_id
        and max(a.start_at, b.start_at) < min(a.end_at, b.end_at, case.cutoff)
    )
    start_times = sorted(t.started_at for t in trips)
    span_minutes = (
        int(
            (datetime.fromisoformat(start_times[-1]) - datetime.fromisoformat(start_times[0])).total_seconds()
            // 60
        )
        if trips
        else 0
    )
    cutoff = datetime.fromisoformat(case.cutoff)
    new_accounts = sum((cutoff - datetime.fromisoformat(r.created_at)).days <= 7 for r in riders)
    totals = {
        "redemption_vnd": sum(r.amount_vnd for r in ledger if r.kind == "redemption"),
        "reversal_vnd": sum(r.amount_vnd for r in ledger if r.kind == "reversal"),
        "net_vnd": sum(r.amount_vnd for r in ledger),
        "trip_count": len(trips),
        "account_count": len(riders),
        "overlap_pairs": overlaps,
        "new_accounts": new_accounts,
        "driver_count": len({t.driver_id for t in trips}),
        "trip_span_minutes": span_minutes,
    }
    expected = {"trips", "devices", "ledger", "accounts"}
    complete = (
        not truncated
        and {q.source_system for q in quality} >= expected
        and all(q.completeness == "complete" and q.as_of >= case.cutoff for q in quality)
    )
    rows = {
        "rider": riders,
        "driver": drivers,
        "device": devices,
        "trip": trips,
        "usage": usage,
        "session": sessions[:500],
        "ledger": ledger,
        "quality": quality,
    }
    sources = [f"{kind}:{r.id}" for kind, records in rows.items() for r in records]
    policies = search_policy(db, actor, case.cutoff, "thiết bị voucher reversal nguồn duyệt", 3) if include_policies else []
    alert = db.scalar(select(Alert).where(Alert.case_id == case.id))
    return ToolEnvelope(
        request_id=uid(),
        source_system="synthetic-operational-snapshot",
        source_record_ids=sources,
        as_of=case.cutoff,
        snapshot_id=case.snapshot_id,
        completeness="complete" if complete else "partial",
        freshness="fixed_snapshot",
        truncated=truncated,
        data={
            "case": serialize(case),
            "alert": serialize(alert) if alert else {},
            "facts": totals,
            "records": {kind: [serialize(r) for r in records] for kind, records in rows.items()},
            "policies": policies,
        },
        error=None if complete else "SOURCE_INCOMPLETE",
    )


def tool_get_case_context(db, actor, case_id: str) -> ToolEnvelope:
    case = require_case(db, actor, case_id)
    alert = db.scalar(select(Alert).where(Alert.case_id == case.id))
    riders = scoped_rows(db, Rider, case)
    drivers = scoped_rows(db, Driver, case)
    devices = scoped_rows(db, Device, case)
    quality = scoped_rows(db, SourceQuality, case)
    comp, err = _check_source_quality(quality, "accounts", case.cutoff)
    cutoff_dt = datetime.fromisoformat(case.cutoff)
    new_accounts = sum((cutoff_dt - datetime.fromisoformat(r.created_at)).days <= 7 for r in riders)
    source_ids = (
        [f"rider:{r.id}" for r in riders]
        + [f"driver:{d.id}" for d in drivers]
        + [f"device:{dv.id}" for dv in devices]
        + [f"quality:{q.id}" for q in quality]
    )
    return ToolEnvelope(
        request_id=uid(),
        source_system="accounts",
        source_record_ids=source_ids,
        as_of=case.cutoff,
        snapshot_id=case.snapshot_id,
        completeness=comp,
        freshness="fixed_snapshot",
        truncated=False,
        data={
            "case": serialize(case),
            "alert": serialize(alert) if alert else {},
            "riders": [serialize(r) for r in riders],
            "drivers": [serialize(d) for d in drivers],
            "devices": [serialize(dv) for dv in devices],
            "quality": [serialize(q) for q in quality],
            "account_count": len(riders),
            "new_accounts": new_accounts,
        },
        error=err,
    )


def tool_get_trip_history(
    db,
    actor,
    case_id: str,
    entity_ids: list[str] | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    cursor: str | None = None,
    page_size: int = 20,
) -> ToolEnvelope:
    case = require_case(db, actor, case_id)
    quality = scoped_rows(db, SourceQuality, case)
    comp, err = _check_source_quality(quality, "trips", case.cutoff)
    if comp == "unavailable":
        return ToolEnvelope(
            request_id=uid(),
            source_system="trips",
            source_record_ids=[f"quality:{q.id}" for q in quality if q.source_system == "trips"],
            as_of=case.cutoff,
            snapshot_id=case.snapshot_id,
            completeness="unavailable",
            freshness="fixed_snapshot",
            data={"items": [], "trip_count": 0, "driver_count": 0, "trip_span_minutes": 0},
            error=err,
        )
    riders = scoped_rows(db, Rider, case)
    drivers = scoped_rows(db, Driver, case)
    allowed_entities = {r.id for r in riders} | {d.id for d in drivers}
    if entity_ids and not _validate_entity_scope(case.id, allowed_entities, entity_ids):
        return ToolEnvelope(
            request_id=uid(),
            source_system="trips",
            source_record_ids=[],
            as_of=case.cutoff,
            snapshot_id=case.snapshot_id,
            completeness="unavailable",
            freshness="fixed_snapshot",
            data={},
            error="SCOPE_VIOLATION",
        )
    effective_end = min(end_at, case.cutoff) if end_at else case.cutoff
    all_trips = [
        t
        for t in scoped_rows(db, Trip, case)
        if t.started_at <= effective_end and (not start_at or t.started_at >= start_at)
    ]
    if entity_ids:
        eset = set(entity_ids)
        all_trips = [t for t in all_trips if t.rider_id in eset or t.driver_id in eset]
    all_trips.sort(key=lambda t: (t.started_at, t.id))

    offset = 0
    if cursor:
        if not cursor.isdigit():
            return ToolEnvelope(
                request_id=uid(),
                source_system="trips",
                source_record_ids=[],
                as_of=case.cutoff,
                snapshot_id=case.snapshot_id,
                completeness="unavailable",
                freshness="fixed_snapshot",
                data={},
                error="INVALID_CURSOR",
            )
        offset = int(cursor)

    bounded_size = min(max(page_size, 1), 50)
    page = all_trips[offset : offset + bounded_size]
    has_more = (offset + bounded_size) < len(all_trips)
    next_cursor = str(offset + bounded_size) if has_more else None

    start_times = sorted(t.started_at for t in all_trips)
    span_minutes = (
        int(
            (datetime.fromisoformat(start_times[-1]) - datetime.fromisoformat(start_times[0])).total_seconds()
            // 60
        )
        if all_trips
        else 0
    )
    return ToolEnvelope(
        request_id=uid(),
        source_system="trips",
        source_record_ids=[f"trip:{t.id}" for t in page],
        as_of=case.cutoff,
        snapshot_id=case.snapshot_id,
        completeness=comp,
        freshness="fixed_snapshot",
        truncated=has_more,
        next_cursor=next_cursor,
        data={
            "items": [serialize(t) for t in page],
            "empty": len(all_trips) == 0,
            "trip_count": len(all_trips),
            "driver_count": len({t.driver_id for t in all_trips}),
            "trip_span_minutes": span_minutes,
        },
        error=err,
    )


def tool_get_device_usage_timeline(
    db,
    actor,
    case_id: str,
    entity_ids: list[str] | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
) -> ToolEnvelope:
    case = require_case(db, actor, case_id)
    quality = scoped_rows(db, SourceQuality, case)
    comp, err = _check_source_quality(quality, "devices", case.cutoff)
    if comp == "unavailable":
        return ToolEnvelope(
            request_id=uid(),
            source_system="devices",
            source_record_ids=[f"quality:{q.id}" for q in quality if q.source_system == "devices"],
            as_of=case.cutoff,
            snapshot_id=case.snapshot_id,
            completeness="unavailable",
            freshness="fixed_snapshot",
            data={"usage": [], "sessions": [], "overlap_pairs": 0},
            error=err,
        )
    riders = scoped_rows(db, Rider, case)
    devices = scoped_rows(db, Device, case)
    allowed_entities = {r.id for r in riders} | {d.id for d in devices}
    if entity_ids and not _validate_entity_scope(case.id, allowed_entities, entity_ids):
        return ToolEnvelope(
            request_id=uid(),
            source_system="devices",
            source_record_ids=[],
            as_of=case.cutoff,
            snapshot_id=case.snapshot_id,
            completeness="unavailable",
            freshness="fixed_snapshot",
            data={},
            error="SCOPE_VIOLATION",
        )
    effective_end = min(end_at, case.cutoff) if end_at else case.cutoff
    usage = [
        u
        for u in scoped_rows(db, DeviceUsage, case)
        if u.start_at <= effective_end and (not start_at or u.end_at >= start_at)
    ]
    sessions = [
        s
        for s in scoped_rows(db, SessionEvent, case)
        if s.occurred_at <= effective_end and (not start_at or s.occurred_at >= start_at)
    ]
    if entity_ids:
        eset = set(entity_ids)
        usage = [u for u in usage if u.rider_id in eset or u.device_id in eset]
        sessions = [s for s in sessions if s.rider_id in eset or s.device_id in eset]

    overlaps = sum(
        1
        for a, b in combinations(usage, 2)
        if a.device_id == b.device_id
        and a.rider_id != b.rider_id
        and max(a.start_at, b.start_at) < min(a.end_at, b.end_at, effective_end)
    )
    ip_prefixes = sorted({s.ip_prefix for s in sessions})
    return ToolEnvelope(
        request_id=uid(),
        source_system="devices",
        source_record_ids=[f"usage:{u.id}" for u in usage] + [f"session:{s.id}" for s in sessions],
        as_of=case.cutoff,
        snapshot_id=case.snapshot_id,
        completeness=comp,
        freshness="fixed_snapshot",
        truncated=False,
        data={
            "usage": [serialize(u) for u in usage],
            "sessions": [serialize(s) for s in sessions],
            "empty": len(usage) == 0 and len(sessions) == 0,
            "overlap_pairs": overlaps,
            "sequential_handoff": overlaps == 0 and len(usage) >= 2,
            "shared_ip_prefixes": ip_prefixes,
        },
        error=err,
    )


def tool_get_relationship_subgraph(
    db,
    actor,
    case_id: str,
    entity_ids: list[str] | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    max_hops: int = 2,
) -> ToolEnvelope:
    case = require_case(db, actor, case_id)
    riders = scoped_rows(db, Rider, case)
    drivers = scoped_rows(db, Driver, case)
    devices = scoped_rows(db, Device, case)
    campaigns = scoped_rows(db, Campaign, case)
    allowed_entities = (
        {r.id for r in riders}
        | {d.id for d in drivers}
        | {dv.id for dv in devices}
        | {c.id for c in campaigns}
    )
    if entity_ids and not _validate_entity_scope(case.id, allowed_entities, entity_ids):
        return ToolEnvelope(
            request_id=uid(),
            source_system="graph",
            source_record_ids=[],
            as_of=case.cutoff,
            snapshot_id=case.snapshot_id,
            completeness="unavailable",
            freshness="fixed_snapshot",
            data={},
            error="SCOPE_VIOLATION",
        )
    effective_end = min(end_at, case.cutoff) if end_at else case.cutoff
    usage = [u for u in scoped_rows(db, DeviceUsage, case) if u.start_at <= effective_end]
    trips = [t for t in scoped_rows(db, Trip, case) if t.started_at <= effective_end]
    sessions = [s for s in scoped_rows(db, SessionEvent, case) if s.occurred_at <= effective_end]
    ledger = [le for le in scoped_rows(db, LedgerEntry, case) if le.occurred_at <= effective_end]

    nodes_map: dict[str, dict] = {}
    for r in riders:
        nodes_map[r.id] = {
            "id": r.id,
            "kind": "rider",
            "label": r.display_name,
            "source_ref": f"rider:{r.id}",
            "note": "Thực thể cần đối chiếu (chưa kết luận)",
        }
    for d in drivers:
        nodes_map[d.id] = {
            "id": d.id,
            "kind": "driver",
            "label": d.display_name,
            "source_ref": f"driver:{d.id}",
            "note": "Tài xế liên quan chuyến đi",
        }
    for dv in devices:
        nodes_map[dv.id] = {
            "id": dv.id,
            "kind": "device",
            "label": dv.label,
            "source_ref": f"device:{dv.id}",
            "note": "Thiết bị ghi nhận trong phạm vi",
        }
    for s in sessions:
        ip_node_id = f"ip:{s.ip_prefix}"
        if ip_node_id not in nodes_map:
            nodes_map[ip_node_id] = {
                "id": ip_node_id,
                "kind": "ip_network",
                "label": f"Mạng {s.ip_prefix}",
                "source_ref": f"session:{s.id}",
                "note": "Tín hiệu mạng/IP (tín hiệu yếu)",
            }

    all_edges: list[dict] = []
    adj: dict[str, set[str]] = {nid: set() for nid in nodes_map}
    for u in usage:
        all_edges.append(
            {
                "source": u.rider_id,
                "target": u.device_id,
                "relation": "used_device",
                "source_ref": f"usage:{u.id}",
            }
        )
        adj.setdefault(u.rider_id, set()).add(u.device_id)
        adj.setdefault(u.device_id, set()).add(u.rider_id)
    for t in trips:
        all_edges.append(
            {
                "source": t.rider_id,
                "target": t.driver_id,
                "relation": "trip_with_driver",
                "source_ref": f"trip:{t.id}",
            }
        )
        adj.setdefault(t.rider_id, set()).add(t.driver_id)
        adj.setdefault(t.driver_id, set()).add(t.rider_id)
    for s in sessions:
        ip_node_id = f"ip:{s.ip_prefix}"
        all_edges.append(
            {
                "source": s.rider_id,
                "target": ip_node_id,
                "relation": "connected_from_ip",
                "source_ref": f"session:{s.id}",
            }
        )
        adj.setdefault(s.rider_id, set()).add(ip_node_id)
        adj.setdefault(ip_node_id, set()).add(s.rider_id)

    hops = min(max(max_hops, 1), 3)
    seeds = list(entity_ids) if entity_ids else [r.id for r in riders]
    visited: set[str] = set(seeds)
    queue = deque((nid, 0) for nid in seeds if nid in nodes_map)
    while queue:
        curr, depth = queue.popleft()
        if depth >= hops:
            continue
        for nxt in sorted(adj.get(curr, set())):
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, depth + 1))

    max_nodes, max_edges = 50, 100
    selected_nodes = [nodes_map[nid] for nid in sorted(visited) if nid in nodes_map]
    filtered_edges = [
        e for e in all_edges if e["source"] in visited and e["target"] in visited
    ]
    truncated = len(selected_nodes) > max_nodes or len(filtered_edges) > max_edges
    selected_nodes = selected_nodes[:max_nodes]
    filtered_edges = filtered_edges[:max_edges]
    refs = sorted(
        {n["source_ref"] for n in selected_nodes}
        | {e["source_ref"] for e in filtered_edges}
        | {f"ledger:{le.id}" for le in ledger[:10]}
    )
    return ToolEnvelope(
        request_id=uid(),
        source_system="graph",
        source_record_ids=refs,
        as_of=case.cutoff,
        snapshot_id=case.snapshot_id,
        completeness="complete",
        freshness="fixed_snapshot",
        truncated=truncated,
        data={
            "nodes": selected_nodes,
            "edges": filtered_edges,
            "max_hops": hops,
            "node_count": len(selected_nodes),
            "edge_count": len(filtered_edges),
        },
        error="TRUNCATED" if truncated else None,
    )


def tool_get_promo_ledger(
    db,
    actor,
    case_id: str,
    entity_ids: list[str] | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
) -> ToolEnvelope:
    case = require_case(db, actor, case_id)
    quality = scoped_rows(db, SourceQuality, case)
    comp, err = _check_source_quality(quality, "ledger", case.cutoff)
    if comp == "unavailable":
        return ToolEnvelope(
            request_id=uid(),
            source_system="ledger",
            source_record_ids=[f"quality:{q.id}" for q in quality if q.source_system == "ledger"],
            as_of=case.cutoff,
            snapshot_id=case.snapshot_id,
            completeness="unavailable",
            freshness="fixed_snapshot",
            data={"entries": [], "redemption_vnd": 0, "reversal_vnd": 0, "net_vnd": 0},
            error=err,
        )
    riders = scoped_rows(db, Rider, case)
    trips = scoped_rows(db, Trip, case)
    allowed_entities = {r.id for r in riders} | {t.id for t in trips}
    if entity_ids and not _validate_entity_scope(case.id, allowed_entities, entity_ids):
        return ToolEnvelope(
            request_id=uid(),
            source_system="ledger",
            source_record_ids=[],
            as_of=case.cutoff,
            snapshot_id=case.snapshot_id,
            completeness="unavailable",
            freshness="fixed_snapshot",
            data={},
            error="SCOPE_VIOLATION",
        )
    effective_end = min(end_at, case.cutoff) if end_at else case.cutoff
    trip_by_id = {t.id: t for t in trips}
    entries = [
        le
        for le in scoped_rows(db, LedgerEntry, case)
        if le.occurred_at <= effective_end and (not start_at or le.occurred_at >= start_at)
    ]
    if entity_ids:
        eset = set(entity_ids)
        entries = [
            le
            for le in entries
            if le.trip_id in eset or (le.trip_id in trip_by_id and trip_by_id[le.trip_id].rider_id in eset)
        ]
    redemption_vnd = sum(le.amount_vnd for le in entries if le.kind == "redemption")
    reversal_vnd = sum(le.amount_vnd for le in entries if le.kind == "reversal")
    net_vnd = sum(le.amount_vnd for le in entries)
    return ToolEnvelope(
        request_id=uid(),
        source_system="ledger",
        source_record_ids=[f"ledger:{le.id}" for le in entries],
        as_of=case.cutoff,
        snapshot_id=case.snapshot_id,
        completeness=comp,
        freshness="fixed_snapshot",
        truncated=False,
        data={
            "entries": [serialize(le) for le in entries],
            "empty": len(entries) == 0,
            "redemption_vnd": redemption_vnd,
            "reversal_vnd": reversal_vnd,
            "net_vnd": net_vnd,
            "redemption_count": sum(1 for le in entries if le.kind == "redemption"),
            "reversal_count": sum(1 for le in entries if le.kind == "reversal"),
        },
        error=err,
    )


def tool_search_policy(
    db,
    actor,
    case_id: str,
    query: str,
    effective_at: str | None = None,
    top_k: int = 4,
    mode: str | None = None,
) -> ToolEnvelope:
    case = require_case(db, actor, case_id)
    target_time = min(effective_at, case.cutoff) if effective_at else case.cutoff
    policies = search_policy(db, actor, target_time, query, top_k=top_k, mode=mode)
    return ToolEnvelope(
        request_id=uid(),
        source_system="policy-rag",
        source_record_ids=[f"policy:{p['id']}" for p in policies],
        as_of=target_time,
        snapshot_id=case.snapshot_id,
        completeness="complete",
        freshness="versioned_policy_store",
        truncated=False,
        data={
            "policies": policies,
            "empty": len(policies) == 0,
            "effective_at": target_time,
        },
        error=None,
    )


def tool_get_source_record(db, actor, case_id: str, source_ref: str) -> ToolEnvelope:
    case = require_case(db, actor, case_id)
    try:
        record = get_source(db, actor, case.id, source_ref)
    except HTTPException:
        return ToolEnvelope(
            request_id=uid(),
            source_system="source-verifier",
            source_record_ids=[],
            as_of=case.cutoff,
            snapshot_id=case.snapshot_id,
            completeness="unavailable",
            freshness="fixed_snapshot",
            data={},
            error="SOURCE_NOT_FOUND_OR_UNAUTHORIZED",
        )
    return ToolEnvelope(
        request_id=uid(),
        source_system="source-verifier",
        source_record_ids=[source_ref],
        as_of=case.cutoff,
        snapshot_id=case.snapshot_id,
        completeness="complete",
        freshness="fixed_snapshot",
        data={"record": record, "source_ref": source_ref},
        error=None,
    )
