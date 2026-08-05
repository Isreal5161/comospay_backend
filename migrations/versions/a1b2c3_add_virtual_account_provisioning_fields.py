"""add virtual account provisioning lifecycle fields


"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "a1b2c3_add_virtual_account_provisioning_fields"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add provisioning lifecycle columns to `virtual_accounts`.
    # Use batch_alter_table for SQLite compatibility and to avoid
    # destructive operations.
    with op.batch_alter_table("virtual_accounts", schema=None) as batch_op:
        # Add `status` with sensible default of 'PENDING' for existing rows
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=50),
                nullable=False,
                server_default=text("'PENDING'"),
            )
        )

        # Number of retries (non-nullable, default 0)
        batch_op.add_column(
            sa.Column(
                "retry_count",
                sa.Integer(),
                nullable=False,
                server_default=text("0"),
            )
        )

        # Timestamps and diagnostic fields (nullable)
        batch_op.add_column(sa.Column("last_retry_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_error", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=True))

    # Optionally remove server defaults so future inserts rely on application models.
    # This is safe in most databases. We attempt to drop the default if supported.
    bind = op.get_bind()
    dialect_name = bind.dialect.name
    if dialect_name not in ("sqlite",):
        # Drop server default for `status` and `retry_count` to defer to ORM defaults
        try:
            with op.batch_alter_table("virtual_accounts") as batch_op:
                batch_op.alter_column("status", server_default=None)
                batch_op.alter_column("retry_count", server_default=None)
        except Exception:
            # If the DB doesn't support altering defaults, leave defaults as-is.
            pass


def downgrade() -> None:
    # Remove the columns added in upgrade. Use batch_alter_table for SQLite.
    with op.batch_alter_table("virtual_accounts", schema=None) as batch_op:
        # Drop in reverse order to be conservative.
        batch_op.drop_column("provisioned_at")
        batch_op.drop_column("last_error")
        batch_op.drop_column("next_retry_at")
        batch_op.drop_column("last_retry_at")
        batch_op.drop_column("retry_count")
        batch_op.drop_column("status")
