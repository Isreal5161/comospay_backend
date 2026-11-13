"""add persistent support tickets and messages

Revision ID: scal010_support_tickets
Revises: scal009_fraud_investigations
"""

from alembic import op
import sqlalchemy as sa


revision = "scal010_support_tickets"
down_revision = "scal009_fraud_investigations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("initial_message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(length=32), nullable=False, server_default="normal"),
        sa.Column("category", sa.String(length=100), nullable=False, server_default="general"),
        sa.Column("assignee_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'waiting_customer', 'resolved', 'closed')",
            name="ck_support_tickets_status",
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_support_tickets_priority",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["assignee_id"], ["admins.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_tickets_user_status", "support_tickets", ["user_id", "status"])
    op.create_index("ix_support_tickets_status_updated", "support_tickets", ["status", "updated_at"])
    op.create_index("ix_support_tickets_priority_updated", "support_tickets", ["priority", "updated_at"])
    op.create_index("ix_support_tickets_assignee_updated", "support_tickets", ["assignee_id", "updated_at"])
    op.create_index("ix_support_tickets_user_id", "support_tickets", ["user_id"])
    op.create_index("ix_support_tickets_created_at", "support_tickets", ["created_at"])
    op.create_index("ix_support_tickets_updated_at", "support_tickets", ["updated_at"])
    op.create_index("ix_support_tickets_category", "support_tickets", ["category"])

    op.create_table(
        "support_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("admin_id", sa.UUID(), nullable=True),
        sa.Column("author_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "author_type IN ('customer', 'admin')",
            name="ck_support_messages_author_type",
        ),
        sa.CheckConstraint(
            "((author_type = 'customer' AND customer_id IS NOT NULL AND admin_id IS NULL) OR (author_type = 'admin' AND admin_id IS NOT NULL AND customer_id IS NULL))",
            name="ck_support_messages_author_identity",
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["support_tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["admin_id"], ["admins.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_messages_ticket_created", "support_messages", ["ticket_id", "created_at"])
    op.create_index("ix_support_messages_ticket_internal_created", "support_messages", ["ticket_id", "is_internal", "created_at"])
    op.create_index("ix_support_messages_customer_id", "support_messages", ["customer_id"])
    op.create_index("ix_support_messages_admin_id", "support_messages", ["admin_id"])


def downgrade() -> None:
    op.drop_index("ix_support_messages_admin_id", table_name="support_messages")
    op.drop_index("ix_support_messages_customer_id", table_name="support_messages")
    op.drop_index("ix_support_messages_ticket_internal_created", table_name="support_messages")
    op.drop_index("ix_support_messages_ticket_created", table_name="support_messages")
    op.drop_table("support_messages")

    op.drop_index("ix_support_tickets_category", table_name="support_tickets")
    op.drop_index("ix_support_tickets_updated_at", table_name="support_tickets")
    op.drop_index("ix_support_tickets_created_at", table_name="support_tickets")
    op.drop_index("ix_support_tickets_user_id", table_name="support_tickets")
    op.drop_index("ix_support_tickets_assignee_updated", table_name="support_tickets")
    op.drop_index("ix_support_tickets_priority_updated", table_name="support_tickets")
    op.drop_index("ix_support_tickets_status_updated", table_name="support_tickets")
    op.drop_index("ix_support_tickets_user_status", table_name="support_tickets")
    op.drop_table("support_tickets")
