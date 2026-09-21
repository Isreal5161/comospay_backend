"""Link withdrawal transactions to trusted bank accounts.

Revision ID: p4d_withdrawal_bank_account_link
Revises: p2e_giftcard_credit_marker
"""

from alembic import op
import sqlalchemy as sa


revision = "p4d_withdrawal_bank_account_link"
down_revision = "p2e_giftcard_credit_marker"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.add_column(sa.Column("bank_account_id", sa.UUID(), nullable=True))
        batch_op.create_index("ix_transactions_bank_account_id", ["bank_account_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_transactions_bank_account_id_bank_accounts",
            "bank_accounts",
            ["bank_account_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_constraint("fk_transactions_bank_account_id_bank_accounts", type_="foreignkey")
        batch_op.drop_index("ix_transactions_bank_account_id")
        batch_op.drop_column("bank_account_id")