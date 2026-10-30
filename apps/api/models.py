from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid() -> str:
    return str(uuid4())


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


json_type = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str]


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    username: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    role: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    csrf_token: Mapped[str]
    expires_at: Mapped[int]


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    snapshot_id: Mapped[str]
    cutoff: Mapped[str]
    title: Mapped[str]
    status: Mapped[str] = mapped_column(default="open")
    report_version: Mapped[int] = mapped_column(default=0)
    disposition: Mapped[str | None]


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), unique=True)
    tenant_id: Mapped[str]
    observed_at: Mapped[str]
    entity_ids: Mapped[list] = mapped_column(json_type)
    rule_ids: Mapped[list] = mapped_column(json_type)
    observed_features: Mapped[dict] = mapped_column(json_type)
    priority_band: Mapped[str]
    data_snapshot_id: Mapped[str]


class ScopedRecord:
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(index=True)
    snapshot_id: Mapped[str]


class Rider(ScopedRecord, Base):
    __tablename__ = "riders"
    created_at: Mapped[str]
    display_name: Mapped[str]


class Driver(ScopedRecord, Base):
    __tablename__ = "drivers"
    display_name: Mapped[str]


class Device(ScopedRecord, Base):
    __tablename__ = "devices"
    label: Mapped[str]


class DeviceUsage(ScopedRecord, Base):
    __tablename__ = "device_usage"
    rider_id: Mapped[str] = mapped_column(ForeignKey("riders.id"))
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    start_at: Mapped[str]
    end_at: Mapped[str]


class SessionEvent(ScopedRecord, Base):
    __tablename__ = "session_events"
    rider_id: Mapped[str] = mapped_column(ForeignKey("riders.id"))
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    ip_prefix: Mapped[str]
    event_type: Mapped[str]
    occurred_at: Mapped[str]
    note: Mapped[str] = mapped_column(Text, default="")


class Trip(ScopedRecord, Base):
    __tablename__ = "trips"
    rider_id: Mapped[str] = mapped_column(ForeignKey("riders.id"))
    driver_id: Mapped[str] = mapped_column(ForeignKey("drivers.id"))
    started_at: Mapped[str]
    ended_at: Mapped[str]
    fare_vnd: Mapped[int] = mapped_column(Integer)
    status: Mapped[str]
    route: Mapped[str]


class Campaign(ScopedRecord, Base):
    __tablename__ = "campaigns"
    name: Mapped[str]


class LedgerEntry(ScopedRecord, Base):
    __tablename__ = "ledger_entries"
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"))
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"))
    occurred_at: Mapped[str]
    kind: Mapped[str]
    amount_vnd: Mapped[int] = mapped_column(Integer)
    reverses_id: Mapped[str | None] = mapped_column(ForeignKey("ledger_entries.id"))


class SourceQuality(ScopedRecord, Base):
    __tablename__ = "source_quality"
    source_system: Mapped[str]
    completeness: Mapped[str]
    as_of: Mapped[str]
    detail: Mapped[str]


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_id: Mapped[str]
    version: Mapped[int]
    tenant_id: Mapped[str]
    allowed_roles: Mapped[list] = mapped_column(json_type)
    effective_from: Mapped[str]
    effective_to: Mapped[str | None]
    section_id: Mapped[str]
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str]


class InvestigationRun(Base):
    __tablename__ = "investigation_runs"
    __table_args__ = (UniqueConstraint("actor_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    tenant_id: Mapped[str]
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    snapshot_id: Mapped[str]
    idempotency_key: Mapped[str]
    status: Mapped[str] = mapped_column(default="queued")
    workflow_mode: Mapped[str] = mapped_column(String(16), default="C")
    provider_mode: Mapped[str] = mapped_column(default="mock")
    retrieval_mode: Mapped[str] = mapped_column(String(32), default="lexical")
    transport: Mapped[str] = mapped_column(String(32), default="direct")
    parent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checkpoint_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    usage_summary: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    created_at: Mapped[str] = mapped_column(default=utcnow)
    error_code: Mapped[str | None]


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("investigation_runs.id"), unique=True)
    status: Mapped[str] = mapped_column(default="queued", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[int] = mapped_column(default=0)
    lease_token: Mapped[str | None]


class Evidence(ScopedRecord, Base):
    __tablename__ = "evidence"
    run_id: Mapped[str] = mapped_column(ForeignKey("investigation_runs.id"))
    kind: Mapped[str]
    source_refs: Mapped[list] = mapped_column(json_type)
    observed_at: Mapped[str]
    retrieved_at: Mapped[str] = mapped_column(default=utcnow)
    structured_values: Mapped[dict] = mapped_column(json_type)
    calculation_ref: Mapped[str]
    completeness: Mapped[str]
    freshness: Mapped[str]


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("case_id", "version"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("investigation_runs.id"), unique=True)
    tenant_id: Mapped[str]
    snapshot_id: Mapped[str]
    version: Mapped[int]
    content_hash: Mapped[str]
    payload: Mapped[dict] = mapped_column(json_type)
    created_at: Mapped[str] = mapped_column(default=utcnow)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (UniqueConstraint("actor_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id"), unique=True)
    report_version: Mapped[int]
    report_hash: Mapped[str]
    snapshot_id: Mapped[str]
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str]
    disposition: Mapped[str]
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(index=True)
    case_id: Mapped[str | None] = mapped_column(index=True)
    run_id: Mapped[str | None]
    actor_id: Mapped[str]
    action: Mapped[str]
    payload: Mapped[dict] = mapped_column(json_type)
    created_at: Mapped[str] = mapped_column(default=utcnow)
