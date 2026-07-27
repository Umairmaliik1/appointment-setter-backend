"""add domain tables for tenant, telephony, chatbot, provisioning

Revision ID: 20260427_0002
Revises: 20260427_0001
Create Date: 2026-04-27 12:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260427_0002"
down_revision = "20260427_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_lower", sa.String(length=255), nullable=True),
        sa.Column("timezone", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tenants_name_lower", "tenants", ["name_lower"], unique=False)

    op.create_table(
        "business_configs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_business_configs_tenant_id", "business_configs", ["tenant_id"], unique=True)

    op.create_table(
        "agent_settings",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_settings_tenant_id", "agent_settings", ["tenant_id"], unique=True)

    op.create_table(
        "twilio_integrations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_twilio_integrations_tenant_id", "twilio_integrations", ["tenant_id"], unique=True)

    op.create_table(
        "agents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agents_tenant_id", "agents", ["tenant_id"], unique=False)

    op.create_table(
        "phone_numbers",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=True),
        sa.Column("phone_number", sa.String(length=64), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_phone_numbers_tenant_id", "phone_numbers", ["tenant_id"], unique=False)
    op.create_index("ix_phone_numbers_agent_id", "phone_numbers", ["agent_id"], unique=False)
    op.create_index("ix_phone_numbers_phone_number", "phone_numbers", ["phone_number"], unique=True)

    op.create_table(
        "appointments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("appointment_datetime", sa.String(length=64), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_appointments_tenant_id", "appointments", ["tenant_id"], unique=False)
    op.create_index("ix_appointments_status", "appointments", ["status"], unique=False)
    op.create_index("ix_appointments_appointment_datetime", "appointments", ["appointment_datetime"], unique=False)

    op.create_table(
        "provisioning_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provisioning_jobs_tenant_id", "provisioning_jobs", ["tenant_id"], unique=False)
    op.create_index("ix_provisioning_jobs_status", "provisioning_jobs", ["status"], unique=False)

    op.create_table(
        "contacts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_contacts_tenant_id", "contacts", ["tenant_id"], unique=False)

    op.create_table(
        "chatbot_agents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("embed_token_version", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chatbot_agents_owner_user_id", "chatbot_agents", ["owner_user_id"], unique=False)
    op.create_index("ix_chatbot_agents_status", "chatbot_agents", ["status"], unique=False)

    op.create_table(
        "chatbot_runtime_logs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("chatbot_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("timestamp", sa.String(length=64), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chatbot_runtime_logs_chatbot_id", "chatbot_runtime_logs", ["chatbot_id"], unique=False)
    op.create_index("ix_chatbot_runtime_logs_status", "chatbot_runtime_logs", ["status"], unique=False)
    op.create_index("ix_chatbot_runtime_logs_timestamp", "chatbot_runtime_logs", ["timestamp"], unique=False)

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "chatbot_chat_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("chatbot_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=True),
        sa.Column("visitor_session_id", sa.String(length=128), nullable=True),
        sa.Column("origin", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("control_mode", sa.String(length=32), nullable=True),
        sa.Column("assigned_operator_id", sa.String(length=64), nullable=True),
        sa.Column("assigned_operator_name", sa.String(length=255), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chatbot_chat_sessions_chatbot_id", "chatbot_chat_sessions", ["chatbot_id"], unique=False)
    op.create_index("ix_chatbot_chat_sessions_owner_user_id", "chatbot_chat_sessions", ["owner_user_id"], unique=False)
    op.create_index("ix_chatbot_chat_sessions_visitor_session_id", "chatbot_chat_sessions", ["visitor_session_id"], unique=False)
    op.create_index("ix_chatbot_chat_sessions_origin", "chatbot_chat_sessions", ["origin"], unique=False)
    op.create_index("ix_chatbot_chat_sessions_status", "chatbot_chat_sessions", ["status"], unique=False)
    op.create_index("ix_chatbot_chat_sessions_control_mode", "chatbot_chat_sessions", ["control_mode"], unique=False)
    op.create_index("ix_chatbot_chat_sessions_assigned_operator_id", "chatbot_chat_sessions", ["assigned_operator_id"], unique=False)

    op.create_table(
        "chatbot_chat_messages",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("sender_type", sa.String(length=32), nullable=True),
        sa.Column("sender_id", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chatbot_chat_messages_session_id", "chatbot_chat_messages", ["session_id"], unique=False)
    op.create_index("ix_chatbot_chat_messages_sender_type", "chatbot_chat_messages", ["sender_type"], unique=False)
    op.create_index("ix_chatbot_chat_messages_sender_id", "chatbot_chat_messages", ["sender_id"], unique=False)
    op.create_index("ix_chatbot_chat_messages_role", "chatbot_chat_messages", ["role"], unique=False)
    op.create_index("ix_chatbot_chat_messages_created_at", "chatbot_chat_messages", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chatbot_chat_messages_sender_id", table_name="chatbot_chat_messages")
    op.drop_index("ix_chatbot_chat_messages_sender_type", table_name="chatbot_chat_messages")
    op.drop_index("ix_chatbot_chat_messages_created_at", table_name="chatbot_chat_messages")
    op.drop_index("ix_chatbot_chat_messages_role", table_name="chatbot_chat_messages")
    op.drop_index("ix_chatbot_chat_messages_session_id", table_name="chatbot_chat_messages")
    op.drop_table("chatbot_chat_messages")
    op.drop_index("ix_chatbot_chat_sessions_assigned_operator_id", table_name="chatbot_chat_sessions")
    op.drop_index("ix_chatbot_chat_sessions_control_mode", table_name="chatbot_chat_sessions")
    op.drop_index("ix_chatbot_chat_sessions_status", table_name="chatbot_chat_sessions")
    op.drop_index("ix_chatbot_chat_sessions_origin", table_name="chatbot_chat_sessions")
    op.drop_index("ix_chatbot_chat_sessions_visitor_session_id", table_name="chatbot_chat_sessions")
    op.drop_index("ix_chatbot_chat_sessions_owner_user_id", table_name="chatbot_chat_sessions")
    op.drop_index("ix_chatbot_chat_sessions_chatbot_id", table_name="chatbot_chat_sessions")
    op.drop_table("chatbot_chat_sessions")
    op.drop_table("system_settings")
    op.drop_index("ix_chatbot_runtime_logs_timestamp", table_name="chatbot_runtime_logs")
    op.drop_index("ix_chatbot_runtime_logs_status", table_name="chatbot_runtime_logs")
    op.drop_index("ix_chatbot_runtime_logs_chatbot_id", table_name="chatbot_runtime_logs")
    op.drop_table("chatbot_runtime_logs")
    op.drop_index("ix_chatbot_agents_status", table_name="chatbot_agents")
    op.drop_index("ix_chatbot_agents_owner_user_id", table_name="chatbot_agents")
    op.drop_table("chatbot_agents")
    op.drop_index("ix_contacts_tenant_id", table_name="contacts")
    op.drop_table("contacts")
    op.drop_index("ix_provisioning_jobs_status", table_name="provisioning_jobs")
    op.drop_index("ix_provisioning_jobs_tenant_id", table_name="provisioning_jobs")
    op.drop_table("provisioning_jobs")
    op.drop_index("ix_appointments_appointment_datetime", table_name="appointments")
    op.drop_index("ix_appointments_status", table_name="appointments")
    op.drop_index("ix_appointments_tenant_id", table_name="appointments")
    op.drop_table("appointments")
    op.drop_index("ix_phone_numbers_phone_number", table_name="phone_numbers")
    op.drop_index("ix_phone_numbers_agent_id", table_name="phone_numbers")
    op.drop_index("ix_phone_numbers_tenant_id", table_name="phone_numbers")
    op.drop_table("phone_numbers")
    op.drop_index("ix_agents_tenant_id", table_name="agents")
    op.drop_table("agents")
    op.drop_index("ix_twilio_integrations_tenant_id", table_name="twilio_integrations")
    op.drop_table("twilio_integrations")
    op.drop_index("ix_agent_settings_tenant_id", table_name="agent_settings")
    op.drop_table("agent_settings")
    op.drop_index("ix_business_configs_tenant_id", table_name="business_configs")
    op.drop_table("business_configs")
    op.drop_index("ix_tenants_name_lower", table_name="tenants")
    op.drop_table("tenants")
