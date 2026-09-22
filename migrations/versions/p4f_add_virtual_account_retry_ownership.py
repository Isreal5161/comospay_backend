"""Add retry lease ownership fields to virtual accounts.

Revision ID: p4f_add_virtual_account_retry_ownership
Revises: p4e_encrypt_bank_account_numbers
"""

from alembic import op
import sqlalchemy as sa


revision = "p4f_add_virtual_account_retry_ownership"
down_revision = "p4e_encrypt_bank_account_numbers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("virtual_accounts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("retry_owner_id", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("retry_claimed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("retry_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_virtual_accounts_retry_owner_id", ["retry_owner_id"], unique=False)
        batch_op.create_index("ix_virtual_accounts_retry_lease_expires_at", ["retry_lease_expires_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("virtual_accounts", schema=None) as batch_op:
        batch_op.drop_index("ix_virtual_accounts_retry_lease_expires_at")
        batch_op.drop_index("ix_virtual_accounts_retry_owner_id")
        batch_op.drop_column("retry_lease_expires_at")
        batch_op.drop_column("retry_claimed_at")
        batch_op.drop_column("retry_owner_id")