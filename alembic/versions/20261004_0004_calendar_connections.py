"""add calendar_connections table

Revision ID: 20261004_0004
Revises: 20260630_0003
Create Date: 2026-10-04 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261004_0004"
down_revision = "20260630_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calendar_connections",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="google"),
        sa.Column("account_email", sa.String(length=320), nullable=False),
        sa.Column("calendar_id", sa.String(length=255), nullable=False, server_default="primary"),
        sa.Column("refresh_token_enc", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_calendar_connections_tenant_id"),
    )
    op.create_index("ix_calendar_connections_tenant_id", "calendar_connections", ["tenant_id"], unique=True)
    op.create_index("ix_calendar_connections_status", "calendar_connections", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_calendar_connections_status", table_name="calendar_connections")
    op.drop_index("ix_calendar_connections_tenant_id", table_name="calendar_connections")
    op.drop_table("calendar_connections")
