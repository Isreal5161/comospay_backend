"""Add separate gift-card sell accounting values.

Revision ID: p2d_giftcard_sell_accounting
Revises: f4a5d6_add_smtp_email_provider
"""

from alembic import op
import sqlalchemy as sa


revision = "p2d_giftcard_sell_accounting"
down_revision = "f4a5d6_add_smtp_email_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.add_column(sa.Column("card_amount", sa.Numeric(12, 2), nullable=True))
        batch_op.add_column(sa.Column("card_currency", sa.String(10), nullable=True))
        batch_op.add_column(sa.Column("payout_amount", sa.Numeric(12, 2), nullable=True))
        batch_op.add_column(sa.Column("payout_currency", sa.String(10), nullable=True))
        batch_op.add_column(sa.Column("credited_amount", sa.Numeric(12, 2), nullable=True))
        batch_op.add_column(sa.Column("credited_currency", sa.String(10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_column("credited_currency")
        batch_op.drop_column("credited_amount")
        batch_op.drop_column("payout_currency")
        batch_op.drop_column("payout_amount")
        batch_op.drop_column("card_currency")
        batch_op.drop_column("card_amount")