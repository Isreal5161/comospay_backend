"""Prepare bank-account numbers for encrypted application-level storage.

Revision ID: p4e_encrypt_bank_account_numbers
Revises: p4d_withdrawal_bank_account_link
"""

from alembic import op
import sqlalchemy as sa


revision = "p4e_encrypt_bank_account_numbers"
down_revision = "p4d_withdrawal_bank_account_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bank_accounts") as batch_op:
        batch_op.alter_column("account_number", new_column_name="account_number_encrypted")
        batch_op.alter_column("account_number_encrypted", type_=sa.String(length=255))
        batch_op.add_column(sa.Column("account_number_fingerprint", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("account_number_prefix", sa.String(length=2), nullable=True))
        batch_op.add_column(sa.Column("account_number_last4", sa.String(length=4), nullable=True))
        batch_op.create_index("ix_bank_accounts_account_number_fingerprint", ["account_number_fingerprint"], unique=False)


def downgrade() -> None:
    raise RuntimeError("Encrypted bank-account storage cannot be downgraded to plaintext storage.")
