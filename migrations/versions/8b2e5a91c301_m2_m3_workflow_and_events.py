"""m2_m3_workflow_and_events"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision = "8b2e5a91c301"
down_revision = "04c4e95b4d66"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "session_events",
        sa.Column("rider_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("ip_prefix", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("snapshot_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"]),
        sa.ForeignKeyConstraint(["rider_id"], ["riders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_session_events_case_id"), "session_events", ["case_id"], unique=False)
    op.create_index(op.f("ix_session_events_tenant_id"), "session_events", ["tenant_id"], unique=False)

    op.add_column(
        "investigation_runs",
        sa.Column("workflow_mode", sa.String(length=16), nullable=False, server_default="C"),
    )
    op.add_column(
        "investigation_runs",
        sa.Column("retrieval_mode", sa.String(length=32), nullable=False, server_default="lexical"),
    )
    op.add_column(
        "investigation_runs",
        sa.Column("transport", sa.String(length=32), nullable=False, server_default="direct"),
    )
    op.add_column(
        "investigation_runs",
        sa.Column("parent_run_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "investigation_runs",
        sa.Column("checkpoint_thread_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "investigation_runs",
        sa.Column(
            "usage_summary",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("investigation_runs", "usage_summary")
    op.drop_column("investigation_runs", "checkpoint_thread_id")
    op.drop_column("investigation_runs", "parent_run_id")
    op.drop_column("investigation_runs", "transport")
    op.drop_column("investigation_runs", "retrieval_mode")
    op.drop_column("investigation_runs", "workflow_mode")
    op.drop_index(op.f("ix_session_events_tenant_id"), table_name="session_events")
    op.drop_index(op.f("ix_session_events_case_id"), table_name="session_events")
    op.drop_table("session_events")

