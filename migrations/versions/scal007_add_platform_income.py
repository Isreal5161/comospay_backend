"""add persisted retained platform-income accounting table

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "scal007_add_platform_income"
down_revision = "p4f_add_virtual_account_retry_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_income",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("transaction_id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_reference", sa.String(length=255), nullable=False),
        sa.Column("income_category", sa.String(length=100), nullable=False),
        sa.Column("provider_identifier", sa.String(length=100), nullable=False),
        sa.Column("provider_reference", sa.String(length=255), nullable=False),
        sa.Column("selling_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("provider_cost", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("gross_margin", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="NGN"),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="unverified"),
        sa.Column("recognized_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("reversal_amount", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.Column("reversal_reference", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], name="fk_platform_income_transaction_id_transactions"),
        sa.CheckConstraint("amount >= 0", name="ck_platform_income_ck_platform_income_amount_non_negative"),
        sa.CheckConstraint("selling_amount > 0", name="ck_platform_income_ck_platform_income_selling_amount_positive"),
        sa.CheckConstraint("provider_cost >= 0", name="ck_platform_income_ck_platform_income_provider_cost_non_negative"),
        sa.CheckConstraint("amount = CASE WHEN gross_margin > 0 THEN gross_margin ELSE 0 END", name="ck_platform_income_ck_platform_income_amount_matches_positive_margin"),
        sa.CheckConstraint("reversal_amount >= 0", name="ck_platform_income_ck_platform_income_reversal_amount_non_negative"),
        sa.CheckConstraint("reversal_amount <= amount", name="ck_platform_income_ck_platform_income_reversal_not_over_amount"),
        sa.UniqueConstraint("transaction_id", "source_type", "income_category", name="uq_platform_income_transaction_source_category"),
        sa.PrimaryKeyConstraint("id", name="pk_platform_income"),
    )
    op.create_index("ix_platform_income_transaction", "platform_income", ["transaction_id", "status", "currency"], unique=False)
    op.create_index("ix_platform_income_status_created", "platform_income", ["status", "created_at"], unique=False)
    op.create_index("ix_platform_income_currency_created", "platform_income", ["currency", "created_at"], unique=False)
    op.create_index("ix_platform_income_id", "platform_income", ["id"], unique=False)
    op.create_index("ix_platform_income_transaction_id", "platform_income", ["transaction_id"], unique=False)
    op.create_index("ix_platform_income_source_type", "platform_income", ["source_type"], unique=False)
    op.create_index("ix_platform_income_source_reference", "platform_income", ["source_reference"], unique=False)
    op.create_index("ix_platform_income_income_category", "platform_income", ["income_category"], unique=False)
    op.create_index("ix_platform_income_provider_reference", "platform_income", ["provider_reference"], unique=False)
    op.create_index("ix_platform_income_currency", "platform_income", ["currency"], unique=False)
    op.create_index("ix_platform_income_status", "platform_income", ["status"], unique=False)
    op.create_index("ix_platform_income_recognized_at", "platform_income", ["recognized_at"], unique=False)
    op.create_index("ix_platform_income_created_at", "platform_income", ["created_at"], unique=False)
    op.create_index("ix_platform_income_updated_at", "platform_income", ["updated_at"], unique=False)
    op.create_table(
        "platform_income_reversals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("platform_income_id", sa.UUID(), nullable=False),
        sa.Column("event_reference", sa.String(length=255), nullable=False),
        sa.Column("source_reference", sa.String(length=255), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["platform_income_id"], ["platform_income.id"], name="fk_platform_income_reversals_platform_income_id_platform_income"),
        sa.CheckConstraint("amount > 0", name="ck_platform_income_reversals_ck_platform_income_reversal_amount_positive"),
        sa.UniqueConstraint("event_reference", name="uq_platform_income_reversal_event_reference"),
        sa.PrimaryKeyConstraint("id", name="pk_platform_income_reversals"),
    )
    op.create_index("ix_platform_income_reversal_income_created", "platform_income_reversals", ["platform_income_id", "created_at"], unique=False)
    op.create_index("ix_platform_income_reversals_id", "platform_income_reversals", ["id"], unique=False)
    op.create_index("ix_platform_income_reversals_platform_income_id", "platform_income_reversals", ["platform_income_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_platform_income_reversals_platform_income_id", table_name="platform_income_reversals")
    op.drop_index("ix_platform_income_reversals_id", table_name="platform_income_reversals")
    op.drop_index("ix_platform_income_reversal_income_created", table_name="platform_income_reversals")
    op.drop_table("platform_income_reversals")
    op.drop_index("ix_platform_income_updated_at", table_name="platform_income")
    op.drop_index("ix_platform_income_created_at", table_name="platform_income")
    op.drop_index("ix_platform_income_recognized_at", table_name="platform_income")
    op.drop_index("ix_platform_income_status", table_name="platform_income")
    op.drop_index("ix_platform_income_currency", table_name="platform_income")
    op.drop_index("ix_platform_income_income_category", table_name="platform_income")
    op.drop_index("ix_platform_income_source_reference", table_name="platform_income")
    op.drop_index("ix_platform_income_source_type", table_name="platform_income")
    op.drop_index("ix_platform_income_provider_reference", table_name="platform_income")
    op.drop_index("ix_platform_income_transaction_id", table_name="platform_income")
    op.drop_index("ix_platform_income_id", table_name="platform_income")
    op.drop_index("ix_platform_income_currency_created", table_name="platform_income")
    op.drop_index("ix_platform_income_status_created", table_name="platform_income")
    op.drop_index("ix_platform_income_transaction", table_name="platform_income")
    op.drop_table("platform_income")
