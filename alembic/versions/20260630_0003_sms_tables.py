"""add SMS app tables for cold-SMS outreach

Revision ID: 20260630_0003
Revises: 20260427_0002
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260630_0003"
down_revision = "20260427_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sms_leads",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("phone_number", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="new"),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "phone_number", name="uq_sms_lead_tenant_phone"),
    )
    op.create_index("ix_sms_leads_tenant_id", "sms_leads", ["tenant_id"], unique=False)
    op.create_index("ix_sms_leads_phone_number", "sms_leads", ["phone_number"], unique=False)
    op.create_index("ix_sms_leads_status", "sms_leads", ["status"], unique=False)

    op.create_table(
        "sms_campaigns",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("from_phone_number", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sms_campaigns_tenant_id", "sms_campaigns", ["tenant_id"], unique=False)
    op.create_index("ix_sms_campaigns_from_phone_number", "sms_campaigns", ["from_phone_number"], unique=False)
    op.create_index("ix_sms_campaigns_status", "sms_campaigns", ["status"], unique=False)

    op.create_table(
        "sms_campaign_enrollments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("lead_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "lead_id", name="uq_sms_enrollment_campaign_lead"),
    )
    op.create_index("ix_sms_campaign_enrollments_tenant_id", "sms_campaign_enrollments", ["tenant_id"], unique=False)
    op.create_index("ix_sms_campaign_enrollments_campaign_id", "sms_campaign_enrollments", ["campaign_id"], unique=False)
    op.create_index("ix_sms_campaign_enrollments_lead_id", "sms_campaign_enrollments", ["lead_id"], unique=False)
    op.create_index("ix_sms_campaign_enrollments_state", "sms_campaign_enrollments", ["state"], unique=False)

    op.create_table(
        "scheduled_sends",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("enrollment_id", sa.String(length=64), nullable=False),
        sa.Column("lead_id", sa.String(length=64), nullable=False),
        sa.Column("to_phone_number", sa.String(length=64), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("send_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="scheduled"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("enrollment_id", "step_index", name="uq_scheduled_send_enrollment_step"),
    )
    op.create_index("ix_scheduled_sends_tenant_id", "scheduled_sends", ["tenant_id"], unique=False)
    op.create_index("ix_scheduled_sends_campaign_id", "scheduled_sends", ["campaign_id"], unique=False)
    op.create_index("ix_scheduled_sends_enrollment_id", "scheduled_sends", ["enrollment_id"], unique=False)
    op.create_index("ix_scheduled_sends_send_after", "scheduled_sends", ["send_after"], unique=False)
    op.create_index("ix_scheduled_sends_status", "scheduled_sends", ["status"], unique=False)
    op.create_index("ix_scheduled_sends_status_send_after", "scheduled_sends", ["status", "send_after"], unique=False)

    op.create_table(
        "sms_messages",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=True),
        sa.Column("lead_id", sa.String(length=64), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("twilio_sid", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("from_phone_number", sa.String(length=64), nullable=True),
        sa.Column("to_phone_number", sa.String(length=64), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sms_messages_tenant_id", "sms_messages", ["tenant_id"], unique=False)
    op.create_index("ix_sms_messages_campaign_id", "sms_messages", ["campaign_id"], unique=False)
    op.create_index("ix_sms_messages_lead_id", "sms_messages", ["lead_id"], unique=False)
    op.create_index("ix_sms_messages_direction", "sms_messages", ["direction"], unique=False)
    op.create_index("ix_sms_messages_twilio_sid", "sms_messages", ["twilio_sid"], unique=True)
    op.create_index("ix_sms_messages_status", "sms_messages", ["status"], unique=False)

    op.create_table(
        "sms_conversations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("lead_id", sa.String(length=64), nullable=True),
        sa.Column("lead_phone_number", sa.String(length=64), nullable=False),
        sa.Column("tenant_phone_number", sa.String(length=64), nullable=False),
        sa.Column("control_mode", sa.String(length=32), nullable=False, server_default="automated"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "lead_phone_number", "tenant_phone_number", name="uq_sms_conversation_thread"
        ),
    )
    op.create_index("ix_sms_conversations_tenant_id", "sms_conversations", ["tenant_id"], unique=False)
    op.create_index("ix_sms_conversations_lead_id", "sms_conversations", ["lead_id"], unique=False)
    op.create_index("ix_sms_conversations_control_mode", "sms_conversations", ["control_mode"], unique=False)
    op.create_index("ix_sms_conversations_status", "sms_conversations", ["status"], unique=False)
    op.create_index("ix_sms_conversations_last_message_at", "sms_conversations", ["last_message_at"], unique=False)

    op.create_table(
        "sms_suppressions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("phone_number", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False, server_default="opted_out"),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "phone_number", name="uq_sms_suppression_tenant_phone"),
    )
    op.create_index("ix_sms_suppressions_tenant_id", "sms_suppressions", ["tenant_id"], unique=False)
    op.create_index("ix_sms_suppressions_phone_number", "sms_suppressions", ["phone_number"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sms_suppressions_phone_number", table_name="sms_suppressions")
    op.drop_index("ix_sms_suppressions_tenant_id", table_name="sms_suppressions")
    op.drop_table("sms_suppressions")

    op.drop_index("ix_sms_conversations_last_message_at", table_name="sms_conversations")
    op.drop_index("ix_sms_conversations_status", table_name="sms_conversations")
    op.drop_index("ix_sms_conversations_control_mode", table_name="sms_conversations")
    op.drop_index("ix_sms_conversations_lead_id", table_name="sms_conversations")
    op.drop_index("ix_sms_conversations_tenant_id", table_name="sms_conversations")
    op.drop_table("sms_conversations")

    op.drop_index("ix_sms_messages_status", table_name="sms_messages")
    op.drop_index("ix_sms_messages_twilio_sid", table_name="sms_messages")
    op.drop_index("ix_sms_messages_direction", table_name="sms_messages")
    op.drop_index("ix_sms_messages_lead_id", table_name="sms_messages")
    op.drop_index("ix_sms_messages_campaign_id", table_name="sms_messages")
    op.drop_index("ix_sms_messages_tenant_id", table_name="sms_messages")
    op.drop_table("sms_messages")

    op.drop_index("ix_scheduled_sends_status_send_after", table_name="scheduled_sends")
    op.drop_index("ix_scheduled_sends_status", table_name="scheduled_sends")
    op.drop_index("ix_scheduled_sends_send_after", table_name="scheduled_sends")
    op.drop_index("ix_scheduled_sends_enrollment_id", table_name="scheduled_sends")
    op.drop_index("ix_scheduled_sends_campaign_id", table_name="scheduled_sends")
    op.drop_index("ix_scheduled_sends_tenant_id", table_name="scheduled_sends")
    op.drop_table("scheduled_sends")

    op.drop_index("ix_sms_campaign_enrollments_state", table_name="sms_campaign_enrollments")
    op.drop_index("ix_sms_campaign_enrollments_lead_id", table_name="sms_campaign_enrollments")
    op.drop_index("ix_sms_campaign_enrollments_campaign_id", table_name="sms_campaign_enrollments")
    op.drop_index("ix_sms_campaign_enrollments_tenant_id", table_name="sms_campaign_enrollments")
    op.drop_table("sms_campaign_enrollments")

    op.drop_index("ix_sms_campaigns_status", table_name="sms_campaigns")
    op.drop_index("ix_sms_campaigns_from_phone_number", table_name="sms_campaigns")
    op.drop_index("ix_sms_campaigns_tenant_id", table_name="sms_campaigns")
    op.drop_table("sms_campaigns")

    op.drop_index("ix_sms_leads_status", table_name="sms_leads")
    op.drop_index("ix_sms_leads_phone_number", table_name="sms_leads")
    op.drop_index("ix_sms_leads_tenant_id", table_name="sms_leads")
    op.drop_table("sms_leads")
