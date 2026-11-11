"""add versioned provider cost configuration and transaction snapshots

Revision ID: scal008_provider_acquisition_costs
Revises: scal007_add_platform_income
"""
from alembic import op
import sqlalchemy as sa


revision = "scal008_provider_acquisition_costs"
down_revision = "scal007_add_platform_income"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_acquisition_costs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider_id", sa.UUID(), nullable=False),
        sa.Column("service_type", sa.String(length=50), nullable=False),
        sa.Column("network_identifier", sa.String(length=120), nullable=True),
        sa.Column("product_identifier", sa.String(length=180), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("cost_method", sa.String(length=50), nullable=False),
        sa.Column("cost_value", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_note", sa.Text(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("cost_value > 0", name="ck_provider_acquisition_cost_value_positive"),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_provider_acquisition_cost_effective_range",
        ),
        sa.CheckConstraint(
            "cost_method IN ('fixed_amount', 'percentage_of_selling_amount')",
            name="ck_provider_acquisition_cost_method",
        ),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["admins.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["admins.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provider_acquisition_costs_provider_id", "provider_acquisition_costs", ["provider_id"])
    op.create_index("ix_provider_acquisition_costs_service_type", "provider_acquisition_costs", ["service_type"])
    op.create_index("ix_provider_acquisition_costs_currency", "provider_acquisition_costs", ["currency"])
    op.create_index("ix_provider_acquisition_costs_is_active", "provider_acquisition_costs", ["is_active"])
    op.create_index("ix_provider_acquisition_costs_effective_from", "provider_acquisition_costs", ["effective_from"])
    op.create_index(
        "ix_provider_acquisition_cost_scope",
        "provider_acquisition_costs",
        ["provider_id", "service_type", "currency", "is_active", "effective_from"],
    )

    op.create_table(
        "provider_cost_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("transaction_id", sa.UUID(), nullable=False),
        sa.Column("provider_id", sa.UUID(), nullable=True),
        sa.Column("configuration_id", sa.UUID(), nullable=True),
        sa.Column("provider_identifier", sa.String(length=120), nullable=True),
        sa.Column("service_type", sa.String(length=50), nullable=False),
        sa.Column("network_identifier", sa.String(length=120), nullable=True),
        sa.Column("product_identifier", sa.String(length=180), nullable=True),
        sa.Column("provider_reference", sa.String(length=255), nullable=True),
        sa.Column("selling_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("cost_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("gross_margin", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("source_class", sa.String(length=40), nullable=False),
        sa.Column("cost_snapshot_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("selling_amount > 0", name="ck_provider_cost_snapshot_selling_positive"),
        sa.CheckConstraint(
            "cost_amount IS NULL OR cost_amount >= 0",
            name="ck_provider_cost_snapshot_cost_non_negative",
        ),
        sa.CheckConstraint(
            "source_class IN ('VERIFIED_ACTUAL', 'VERIFIED_CONTRACTUAL', 'ADMIN_CONFIGURED', 'UNAVAILABLE')",
            name="ck_provider_cost_snapshot_source_class",
        ),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"]),
        sa.ForeignKeyConstraint(["configuration_id"], ["provider_acquisition_costs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transaction_id", name="uq_provider_cost_snapshot_transaction"),
    )
    op.create_index("ix_provider_cost_snapshots_transaction_id", "provider_cost_snapshots", ["transaction_id"])
    op.create_index("ix_provider_cost_snapshots_provider_id", "provider_cost_snapshots", ["provider_id"])
    op.create_index("ix_provider_cost_snapshots_configuration_id", "provider_cost_snapshots", ["configuration_id"])
    op.create_index("ix_provider_cost_snapshots_service_type", "provider_cost_snapshots", ["service_type"])
    op.create_index("ix_provider_cost_snapshots_currency", "provider_cost_snapshots", ["currency"])
    op.create_index("ix_provider_cost_snapshots_source_class", "provider_cost_snapshots", ["source_class"])
    op.create_index(
        "ix_provider_cost_snapshot_provider_service",
        "provider_cost_snapshots",
        ["provider_identifier", "service_type", "currency"],
    )
    op.create_index(
        "ix_provider_cost_snapshot_classified_at",
        "provider_cost_snapshots",
        ["source_class", "cost_snapshot_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_provider_cost_snapshot_classified_at", table_name="provider_cost_snapshots")
    op.drop_index("ix_provider_cost_snapshot_provider_service", table_name="provider_cost_snapshots")
    op.drop_index("ix_provider_cost_snapshots_source_class", table_name="provider_cost_snapshots")
    op.drop_index("ix_provider_cost_snapshots_currency", table_name="provider_cost_snapshots")
    op.drop_index("ix_provider_cost_snapshots_service_type", table_name="provider_cost_snapshots")
    op.drop_index("ix_provider_cost_snapshots_configuration_id", table_name="provider_cost_snapshots")
    op.drop_index("ix_provider_cost_snapshots_provider_id", table_name="provider_cost_snapshots")
    op.drop_index("ix_provider_cost_snapshots_transaction_id", table_name="provider_cost_snapshots")
    op.drop_table("provider_cost_snapshots")
    op.drop_index("ix_provider_acquisition_cost_scope", table_name="provider_acquisition_costs")
    op.drop_index("ix_provider_acquisition_costs_effective_from", table_name="provider_acquisition_costs")
    op.drop_index("ix_provider_acquisition_costs_is_active", table_name="provider_acquisition_costs")
    op.drop_index("ix_provider_acquisition_costs_currency", table_name="provider_acquisition_costs")
    op.drop_index("ix_provider_acquisition_costs_service_type", table_name="provider_acquisition_costs")
    op.drop_index("ix_provider_acquisition_costs_provider_id", table_name="provider_acquisition_costs")
    op.drop_table("provider_acquisition_costs")
