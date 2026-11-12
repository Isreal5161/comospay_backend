"""add persistent fraud investigation state and history

Revision ID: scal009_fraud_investigations
Revises: scal008_provider_acquisition_costs
"""
from alembic import op
import sqlalchemy as sa


revision = "scal009_fraud_investigations"
down_revision = "scal008_provider_acquisition_costs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fraud_investigations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("signal_type", sa.String(length=32), nullable=False),
        sa.Column("signal_reference", sa.UUID(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("reviewed_by_admin_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "signal_type IN ('transaction', 'account_security')",
            name="ck_fraud_investigation_signal_type",
        ),
        sa.CheckConstraint(
            "state IN ('open', 'in_review', 'escalated', 'dismissed', 'resolved')",
            name="ck_fraud_investigation_state",
        ),
        sa.ForeignKeyConstraint(["reviewed_by_admin_id"], ["admins.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signal_type", "signal_reference", name="uq_fraud_investigation_signal"),
    )
    op.create_index(
        "ix_fraud_investigations_state_updated",
        "fraud_investigations",
        ["state", "updated_at"],
    )

    op.create_table(
        "fraud_investigation_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("investigation_id", sa.UUID(), nullable=False),
        sa.Column("admin_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=False),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('review', 'dismiss', 'escalate', 'resolve')",
            name="ck_fraud_investigation_event_action",
        ),
        sa.ForeignKeyConstraint(["admin_id"], ["admins.id"]),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["fraud_investigations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_fraud_investigation_events_investigation_id",
        "fraud_investigation_events",
        ["investigation_id"],
    )
    op.create_index(
        "ix_fraud_investigation_events_case_created",
        "fraud_investigation_events",
        ["investigation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_fraud_investigation_events_case_created", table_name="fraud_investigation_events")
    op.drop_index("ix_fraud_investigation_events_investigation_id", table_name="fraud_investigation_events")
    op.drop_table("fraud_investigation_events")
    op.drop_index("ix_fraud_investigations_state_updated", table_name="fraud_investigations")
    op.drop_table("fraud_investigations")
