from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.models import Alert, Case, DeviceUsage, LedgerEntry, SessionEvent, Trip


def evaluate_case(db: Session, case: Case) -> Alert | None:
    """Upstream signal only. Never produces a fraud label, LLM score, or disposition.

    Priority band is computed deterministically from observable gross promo volume
    and entity linkage count at snapshot cutoff.
    """
    usage = db.scalars(select(DeviceUsage).where(DeviceUsage.case_id == case.id)).all()
    devices: dict[str, set[str]] = {}
    for item in usage:
        devices.setdefault(item.device_id, set()).add(item.rider_id)
    linked = max((len(ids) for ids in devices.values()), default=0)

    ledger = db.scalars(
        select(LedgerEntry).where(LedgerEntry.case_id == case.id, LedgerEntry.occurred_at <= case.cutoff)
    ).all()
    counts = Counter(row.kind for row in ledger)
    redemptions = counts["redemption"]

    trips = db.scalars(
        select(Trip).where(Trip.case_id == case.id, Trip.started_at <= case.cutoff)
    ).all()
    pair_counts = Counter((t.rider_id, t.driver_id) for t in trips)
    max_pair_trips = max(pair_counts.values(), default=0)

    sessions = db.scalars(
        select(SessionEvent).where(SessionEvent.case_id == case.id, SessionEvent.occurred_at <= case.cutoff)
    ).all()
    ip_riders: dict[str, set[str]] = {}
    for s in sessions:
        ip_riders.setdefault(s.ip_prefix, set()).add(s.rider_id)
    max_ip_accounts = max((len(rset) for rset in ip_riders.values()), default=0)

    rule_ids: list[str] = []
    if linked >= 3 and redemptions >= 6:
        rule_ids.append("SHARED_DEVICE_PROMO_V1")
    elif linked >= 2 and redemptions >= 4:
        rule_ids.append("DEVICE_HANDOFF_PROMO_V1")

    if max_ip_accounts >= 3 and redemptions >= 4 and "SHARED_DEVICE_PROMO_V1" not in rule_ids:
        rule_ids.append("SHARED_NETWORK_PROMO_V1")

    if max_pair_trips >= 4 and redemptions >= 4 and not rule_ids:
        rule_ids.append("REPEATED_PAIR_PROMO_V1")

    if not rule_ids:
        return None

    entity_ids = sorted(
        {row.rider_id for row in usage} | {t.rider_id for t in trips} | {s.rider_id for s in sessions}
    )
    priority_band = "high" if (redemptions >= 8 or linked >= 4) else "medium"
    return Alert(
        id=f"alert-{case.id}",
        case_id=case.id,
        tenant_id=case.tenant_id,
        observed_at=case.cutoff,
        entity_ids=entity_ids,
        rule_ids=rule_ids,
        observed_features={
            "linked_accounts": linked if linked > 0 else max_ip_accounts,
            "redemption_count": redemptions,
        },
        priority_band=priority_band,
        data_snapshot_id=case.snapshot_id,
    )
