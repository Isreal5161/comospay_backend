"""Add durable gift-card wallet-credit completion marker.

Revision ID: p2e_giftcard_credit_marker
Revises: p2d_giftcard_sell_accounting
"""

from alembic import op
import sqlalchemy as sa


revision = "p2e_giftcard_credit_marker"
down_revision = "p2d_giftcard_sell_accounting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.add_column(
            sa.Column("credit_applied", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_column("credit_applied")