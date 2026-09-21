"""add provider-reference scoped uniqueness constraint to transactions table


"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "scal006_add_provider_ref_uniqueness"
down_revision = "a1b2c3_add_virtual_account_provisioning_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add UNIQUE constraint on (provider_name, provider_reference) to enforce
    # provider-scoped uniqueness of provider references.
    # This constraint allows multiple NULLs (standard SQL NULL semantics).
    # Different providers can use the same reference value independently.
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_transactions_provider_ref",
            ["provider_name", "provider_reference"],
        )


def downgrade() -> None:
    # Remove the UNIQUE constraint on (provider_name, provider_reference)
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_transactions_provider_ref",
            type_="unique",
        )
