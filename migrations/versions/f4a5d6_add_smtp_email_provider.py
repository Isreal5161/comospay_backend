"""Seed the default SMTP email provider record.

Revision ID: f4a5d6_add_smtp_email_provider
Revises: scal006_add_provider_ref_uniqueness
Create Date: 2026-08-21
"""

from __future__ import annotations

from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "f4a5d6_add_smtp_email_provider"
down_revision = "scal006_add_provider_ref_uniqueness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Ensure the default configured email provider exists in the provider catalog."""
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO providers (
                id,
                code,
                name,
                category,
                description,
                status,
                is_active,
                environment,
                priority,
                metadata_payload,
                health_status,
                created_at,
                updated_at
            )
            VALUES (
                :id,
                :code,
                :name,
                :category,
                :description,
                :status,
                :is_active,
                :environment,
                :priority,
                :metadata_payload,
                :health_status,
                NOW(),
                NOW()
            )
            ON CONFLICT (code) DO NOTHING
            """
        ),
        {
            "id": str(uuid4()),
            "code": "smtp",
            "name": "smtp",
            "category": "Email",
            "description": "Default SMTP email provider for MAIL_PROVIDER=smtp.",
            "status": "active",
            "is_active": True,
            "environment": "production",
            "priority": 0,
            "metadata_payload": '{"health_score": 100.0, "success_rate": 100.0, "failure_count": 0, "maintenance_mode": false}',
            "health_status": "healthy",
        },
    )


def downgrade() -> None:
    """Remove the seeded SMTP email provider record if it was created by this migration."""
    op.execute(
        sa.text(
            """
            DELETE FROM providers
            WHERE code = :code
            AND name = :name
            AND category = :category
            """
        ),
        {"code": "smtp", "name": "smtp", "category": "Email"},
    )
