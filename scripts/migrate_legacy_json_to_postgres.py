"""
Migrate Legacy-exported JSON/NDJSON collections into PostgreSQL.

Usage:
  python scripts/migrate_legacy_json_to_postgres.py --input-dir ./legacy-export --dry-run
  python scripts/migrate_legacy_json_to_postgres.py --input-dir ./legacy-export --apply --report-file ./migration-report.json

Input file naming:
  <collection>.json
  <collection>.jsonl
  <collection>.ndjson
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.services.database import session_scope
from app.services.postgres_models import (  # noqa: E402
    AgentDomainModel,
    AgentSettingsModel,
    AppointmentDomainModel,
    AuditLogModel,
    BusinessConfigModel,
    ChatbotAgentModel,
    ChatbotChatMessageModel,
    ChatbotChatSessionModel,
    ChatbotRuntimeLogModel,
    ContactModel,
    IdempotencyKeyModel,
    OrgMembershipModel,
    OrgModel,
    PartnerEntitlementModel,
    PhoneNumberModel,
    PlatformRoleModel,
    ProvisioningJobModel,
    SystemSettingModel,
    TenantModel,
    TwilioIntegrationModel,
    UserModel,
)


def _parse_iso(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _chunked(items: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _load_documents(file_path: Path) -> List[Dict[str, Any]]:
    ext = file_path.suffix.lower()
    if ext in {".jsonl", ".ndjson"}:
        docs: List[Dict[str, Any]] = []
        with file_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if isinstance(payload, dict):
                    docs.append(payload)
        return docs

    with file_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        # supports {"doc_id": {...}} or {"documents":[...]}
        if "documents" in payload and isinstance(payload["documents"], list):
            return [item for item in payload["documents"] if isinstance(item, dict)]
        docs: List[Dict[str, Any]] = []
        for key, value in payload.items():
            if isinstance(value, dict):
                if "id" not in value:
                    value = {"id": key, **value}
                docs.append(value)
        return docs
    return []


def _load_collection(input_dir: Path, collection_name: str) -> List[Dict[str, Any]]:
    candidates = [
        input_dir / f"{collection_name}.json",
        input_dir / f"{collection_name}.jsonl",
        input_dir / f"{collection_name}.ndjson",
    ]
    for candidate in candidates:
        if candidate.exists():
            return _load_documents(candidate)
    return []


@dataclass
class CollectionSpec:
    collection: str
    model: Any
    key_columns: Tuple[str, ...]


SPECS: List[CollectionSpec] = [
    CollectionSpec("users", UserModel, ("id",)),
    CollectionSpec("orgs", OrgModel, ("id",)),
    CollectionSpec("org_memberships", OrgMembershipModel, ("id",)),
    CollectionSpec("partner_entitlements", PartnerEntitlementModel, ("partner_org_id",)),
    CollectionSpec("platform_roles", PlatformRoleModel, ("id",)),
    CollectionSpec("audit_logs", AuditLogModel, ("id",)),
    CollectionSpec("idempotency_keys", IdempotencyKeyModel, ("id",)),
    CollectionSpec("tenants", TenantModel, ("id",)),
    CollectionSpec("business_configs", BusinessConfigModel, ("id",)),
    CollectionSpec("agent_settings", AgentSettingsModel, ("id",)),
    CollectionSpec("twilio_integrations", TwilioIntegrationModel, ("id",)),
    CollectionSpec("agents", AgentDomainModel, ("id",)),
    CollectionSpec("phone_numbers", PhoneNumberModel, ("id",)),
    CollectionSpec("appointments", AppointmentDomainModel, ("id",)),
    CollectionSpec("provisioning_jobs", ProvisioningJobModel, ("id",)),
    CollectionSpec("contacts", ContactModel, ("id",)),
    CollectionSpec("chatbot_agents", ChatbotAgentModel, ("id",)),
    CollectionSpec("chatbot_runtime_logs", ChatbotRuntimeLogModel, ("id",)),
    CollectionSpec("system_settings", SystemSettingModel, ("key",)),
    CollectionSpec("chatbot_chat_sessions", ChatbotChatSessionModel, ("id",)),
    CollectionSpec("chatbot_chat_messages", ChatbotChatMessageModel, ("id",)),
]


def _to_user_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    first_name = str(doc.get("first_name") or "").strip()
    last_name = str(doc.get("last_name") or "").strip()
    return {
        "id": str(doc.get("id") or ""),
        "email": str(doc.get("email") or "").strip().lower(),
        "username": str(doc.get("username") or "").strip() or str(doc.get("email") or "").split("@")[0],
        "hashed_password": str(doc.get("hashed_password") or "__MISSING_HASH__"),
        "first_name": first_name or "Unknown",
        "last_name": last_name or "User",
        "full_name": str(doc.get("full_name") or f"{first_name} {last_name}".strip() or "Unknown User"),
        "role": str(doc.get("role") or "user"),
        "status": str(doc.get("status") or "active"),
        "is_active": bool(doc.get("is_active", True)),
        "is_verified": bool(doc.get("is_verified", False)),
        "is_email_verified": bool(doc.get("is_email_verified", False)),
        "tenant_id": doc.get("tenant_id"),
        "allowed_app_ids": doc.get("allowed_app_ids") or [],
        "default_app_id": doc.get("default_app_id"),
        "platform_role_id": doc.get("platform_role_id"),
        "active_org_id": doc.get("active_org_id"),
        "last_login": doc.get("last_login"),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_org_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "org_type": str(doc.get("org_type") or "customer"),
        "parent_org_id": doc.get("parent_org_id"),
        "legacy_tenant_id": doc.get("legacy_tenant_id"),
        "name": str(doc.get("name") or ""),
        "status": str(doc.get("status") or "active"),
        "branding": doc.get("branding") or {},
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_org_membership_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "org_id": str(doc.get("org_id") or ""),
        "user_id": str(doc.get("user_id") or ""),
        "role": str(doc.get("role") or "customer_staff"),
        "status": str(doc.get("status") or "active"),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_partner_entitlements_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "partner_org_id": str(doc.get("partner_org_id") or doc.get("id") or ""),
        "appointment_setter_enabled": bool(doc.get("appointment_setter_enabled", True)),
        "approved_by_user_id": doc.get("approved_by_user_id"),
        "approval_notes": doc.get("approval_notes"),
        "onboarding_status": doc.get("onboarding_status"),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_platform_role_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "name": str(doc.get("name") or ""),
        "slug": str(doc.get("slug") or ""),
        "scope": str(doc.get("scope") or "platform"),
        "description": doc.get("description"),
        "permissions": doc.get("permissions") or [],
        "status": str(doc.get("status") or "active"),
        "is_system": bool(doc.get("is_system", False)),
        "created_by_user_id": str(doc.get("created_by_user_id") or "system"),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_audit_log_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "action": str(doc.get("action") or ""),
        "resource_type": str(doc.get("resource_type") or ""),
        "resource_id": str(doc.get("resource_id") or ""),
        "status": str(doc.get("status") or "success"),
        "actor": doc.get("actor") or {},
        "metadata": doc.get("metadata") or {},
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_idempotency_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "scope": str(doc.get("scope") or "unknown"),
        "key": str(doc.get("key") or ""),
        "request_hash": str(doc.get("request_hash") or ""),
        "status": str(doc.get("status") or "in_progress"),
        "user_id": doc.get("user_id"),
        "error_message": doc.get("error_message"),
        "response_payload": doc.get("response_payload"),
        "completed_at": _parse_iso(doc.get("completed_at")),
        "failed_at": _parse_iso(doc.get("failed_at")),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_tenant_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(doc)
    return {
        "id": str(doc.get("id") or ""),
        "name": str(doc.get("name") or ""),
        "name_lower": doc.get("name_lower"),
        "timezone": doc.get("timezone"),
        "status": doc.get("status"),
        "data": payload,
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_data_row(doc: Dict[str, Any], id_field: str, tenant_field: Optional[str] = None) -> Dict[str, Any]:
    payload = dict(doc)
    row: Dict[str, Any] = {
        id_field: str(doc.get(id_field) or ""),
        "data": payload,
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }
    if tenant_field is not None:
        row[tenant_field] = doc.get(tenant_field) or ""
    return row


def _to_business_config_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "tenant_id": str(doc.get("tenant_id") or ""),
        "data": dict(doc),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_agent_settings_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "tenant_id": str(doc.get("tenant_id") or ""),
        "data": dict(doc),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_twilio_integration_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "tenant_id": str(doc.get("tenant_id") or ""),
        "data": dict(doc),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_agent_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "tenant_id": str(doc.get("tenant_id") or ""),
        "data": dict(doc),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_phone_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "tenant_id": str(doc.get("tenant_id") or ""),
        "agent_id": doc.get("agent_id"),
        "phone_number": str(doc.get("phone_number") or ""),
        "data": dict(doc),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_appointment_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "tenant_id": str(doc.get("tenant_id") or ""),
        "status": doc.get("status"),
        "appointment_datetime": doc.get("appointment_datetime"),
        "data": dict(doc),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_provisioning_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "tenant_id": doc.get("tenant_id"),
        "status": doc.get("status"),
        "data": dict(doc),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_contact_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "tenant_id": doc.get("tenant_id"),
        "data": dict(doc),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_chatbot_agent_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "owner_user_id": str(doc.get("owner_user_id") or ""),
        "status": doc.get("status"),
        "embed_token_version": int(doc.get("embed_token_version") or 1),
        "data": dict(doc),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_runtime_log_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "chatbot_id": str(doc.get("chatbot_id") or ""),
        "status": doc.get("status"),
        "timestamp": doc.get("timestamp"),
        "data": dict(doc),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_system_settings_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(doc)
    key = str(doc.get("key") or doc.get("id") or "chatbot_runtime")
    return {"key": key, "data": data, "updated_at": _parse_iso(doc.get("updated_at")) or _now()}


def _to_chat_session_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "chatbot_id": str(doc.get("chatbot_id") or ""),
        "owner_user_id": doc.get("owner_user_id"),
        "visitor_session_id": doc.get("visitor_session_id"),
        "origin": doc.get("origin"),
        "status": doc.get("status"),
        "control_mode": doc.get("control_mode"),
        "assigned_operator_id": doc.get("assigned_operator_id"),
        "assigned_operator_name": doc.get("assigned_operator_name"),
        "data": dict(doc),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


def _to_chat_message_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("id") or ""),
        "session_id": str(doc.get("session_id") or ""),
        "sender_type": doc.get("sender_type"),
        "sender_id": doc.get("sender_id"),
        "role": doc.get("role"),
        "content": doc.get("content"),
        "data": dict(doc),
        "created_at": _parse_iso(doc.get("created_at")) or _now(),
        "updated_at": _parse_iso(doc.get("updated_at")) or _now(),
    }


ROW_MAPPERS = {
    "users": _to_user_row,
    "orgs": _to_org_row,
    "org_memberships": _to_org_membership_row,
    "partner_entitlements": _to_partner_entitlements_row,
    "platform_roles": _to_platform_role_row,
    "audit_logs": _to_audit_log_row,
    "idempotency_keys": _to_idempotency_row,
    "tenants": _to_tenant_row,
    "business_configs": _to_business_config_row,
    "agent_settings": _to_agent_settings_row,
    "twilio_integrations": _to_twilio_integration_row,
    "agents": _to_agent_row,
    "phone_numbers": _to_phone_row,
    "appointments": _to_appointment_row,
    "provisioning_jobs": _to_provisioning_row,
    "contacts": _to_contact_row,
    "chatbot_agents": _to_chatbot_agent_row,
    "chatbot_runtime_logs": _to_runtime_log_row,
    "system_settings": _to_system_settings_row,
    "chatbot_chat_sessions": _to_chat_session_row,
    "chatbot_chat_messages": _to_chat_message_row,
}


def _filter_valid_rows(spec: CollectionSpec, rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    valid: List[Dict[str, Any]] = []
    invalid = 0
    for row in rows:
        missing_pk = any(not str(row.get(k, "")).strip() for k in spec.key_columns)
        if missing_pk:
            invalid += 1
            continue
        valid.append(row)
    return valid, invalid


def _upsert_rows(spec: CollectionSpec, rows: List[Dict[str, Any]], chunk_size: int = 1000) -> int:
    table = spec.model.__table__
    inserted = 0
    with session_scope() as session:
        for chunk in _chunked(rows, chunk_size):
            stmt = pg_insert(table).values(chunk)
            update_columns = {c.name: stmt.excluded[c.name] for c in table.columns if c.name not in spec.key_columns}
            stmt = stmt.on_conflict_do_update(index_elements=list(spec.key_columns), set_=update_columns)
            session.execute(stmt)
            inserted += len(chunk)
    return inserted


def _count_rows(model: Any) -> int:
    with session_scope() as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _integrity_checks() -> Dict[str, int]:
    checks: Dict[str, int] = {}
    with session_scope() as session:
        checks["org_memberships_missing_user"] = int(
            session.scalar(
                select(func.count()).select_from(OrgMembershipModel).where(
                    ~OrgMembershipModel.user_id.in_(select(UserModel.id))
                )
            )
            or 0
        )
        checks["org_memberships_missing_org"] = int(
            session.scalar(
                select(func.count()).select_from(OrgMembershipModel).where(
                    ~OrgMembershipModel.org_id.in_(select(OrgModel.id))
                )
            )
            or 0
        )
        checks["users_missing_platform_role"] = int(
            session.scalar(
                select(func.count()).select_from(UserModel).where(
                    UserModel.platform_role_id.is_not(None),
                    ~UserModel.platform_role_id.in_(select(PlatformRoleModel.id)),
                )
            )
            or 0
        )
        checks["users_missing_active_org"] = int(
            session.scalar(
                select(func.count()).select_from(UserModel).where(
                    UserModel.active_org_id.is_not(None),
                    ~UserModel.active_org_id.in_(select(OrgModel.id)),
                )
            )
            or 0
        )
        checks["orgs_missing_parent_org"] = int(
            session.scalar(
                select(func.count()).select_from(OrgModel).where(
                    OrgModel.parent_org_id.is_not(None),
                    ~OrgModel.parent_org_id.in_(select(OrgModel.id)),
                )
            )
            or 0
        )
        checks["partner_entitlements_missing_partner_org"] = int(
            session.scalar(
                select(func.count()).select_from(PartnerEntitlementModel).where(
                    ~PartnerEntitlementModel.partner_org_id.in_(select(OrgModel.id))
                )
            )
            or 0
        )
        checks["agents_missing_tenant"] = int(
            session.scalar(
                select(func.count()).select_from(AgentDomainModel).where(
                    ~AgentDomainModel.tenant_id.in_(select(TenantModel.id))
                )
            )
            or 0
        )
        checks["phone_numbers_missing_tenant"] = int(
            session.scalar(
                select(func.count()).select_from(PhoneNumberModel).where(
                    ~PhoneNumberModel.tenant_id.in_(select(TenantModel.id))
                )
            )
            or 0
        )
        checks["phone_numbers_missing_agent"] = int(
            session.scalar(
                select(func.count()).select_from(PhoneNumberModel).where(
                    PhoneNumberModel.agent_id.is_not(None),
                    ~PhoneNumberModel.agent_id.in_(select(AgentDomainModel.id)),
                )
            )
            or 0
        )
        checks["appointments_missing_tenant"] = int(
            session.scalar(
                select(func.count()).select_from(AppointmentDomainModel).where(
                    ~AppointmentDomainModel.tenant_id.in_(select(TenantModel.id))
                )
            )
            or 0
        )
        checks["business_configs_missing_tenant"] = int(
            session.scalar(
                select(func.count()).select_from(BusinessConfigModel).where(
                    ~BusinessConfigModel.tenant_id.in_(select(TenantModel.id))
                )
            )
            or 0
        )
        checks["agent_settings_missing_tenant"] = int(
            session.scalar(
                select(func.count()).select_from(AgentSettingsModel).where(
                    ~AgentSettingsModel.tenant_id.in_(select(TenantModel.id))
                )
            )
            or 0
        )
        checks["twilio_integrations_missing_tenant"] = int(
            session.scalar(
                select(func.count()).select_from(TwilioIntegrationModel).where(
                    ~TwilioIntegrationModel.tenant_id.in_(select(TenantModel.id))
                )
            )
            or 0
        )
        checks["chatbot_agents_missing_owner"] = int(
            session.scalar(
                select(func.count()).select_from(ChatbotAgentModel).where(
                    ~ChatbotAgentModel.owner_user_id.in_(select(UserModel.id))
                )
            )
            or 0
        )
        checks["chatbot_sessions_missing_chatbot"] = int(
            session.scalar(
                select(func.count()).select_from(ChatbotChatSessionModel).where(
                    ~ChatbotChatSessionModel.chatbot_id.in_(select(ChatbotAgentModel.id))
                )
            )
            or 0
        )
        checks["chatbot_messages_missing_session"] = int(
            session.scalar(
                select(func.count()).select_from(ChatbotChatMessageModel).where(
                    ~ChatbotChatMessageModel.session_id.in_(select(ChatbotChatSessionModel.id))
                )
            )
            or 0
        )
        checks["runtime_logs_missing_chatbot"] = int(
            session.scalar(
                select(func.count()).select_from(ChatbotRuntimeLogModel).where(
                    ~ChatbotRuntimeLogModel.chatbot_id.in_(select(ChatbotAgentModel.id))
                )
            )
            or 0
        )
    return checks


def run_migration(input_dir: Path, apply: bool, report_file: Optional[Path], chunk_size: int) -> Dict[str, Any]:
    source_counts: Dict[str, int] = {}
    transformed_counts: Dict[str, int] = {}
    invalid_counts: Dict[str, int] = {}
    inserted_counts: Dict[str, int] = {}
    warnings: Dict[str, List[str]] = defaultdict(list)

    for spec in SPECS:
        docs = _load_collection(input_dir, spec.collection)
        source_counts[spec.collection] = len(docs)
        transformer = ROW_MAPPERS[spec.collection]
        transformed = [transformer(doc) for doc in docs]
        valid_rows, invalid = _filter_valid_rows(spec, transformed)
        transformed_counts[spec.collection] = len(valid_rows)
        invalid_counts[spec.collection] = invalid
        if invalid:
            warnings[spec.collection].append(f"{invalid} rows skipped due to missing PK fields {spec.key_columns}")

        if apply and valid_rows:
            inserted = _upsert_rows(spec, valid_rows, chunk_size=chunk_size)
        else:
            inserted = len(valid_rows)
        inserted_counts[spec.collection] = inserted

    db_counts = {spec.collection: _count_rows(spec.model) for spec in SPECS}
    integrity = _integrity_checks()

    report: Dict[str, Any] = {
        "generated_at": _now().isoformat(),
        "mode": "apply" if apply else "dry_run",
        "input_dir": str(input_dir),
        "collections": [],
        "integrity_checks": integrity,
        "warnings": warnings,
    }

    for spec in SPECS:
        report["collections"].append(
            {
                "collection": spec.collection,
                "source_count": source_counts[spec.collection],
                "transformed_valid_count": transformed_counts[spec.collection],
                "invalid_skipped_count": invalid_counts[spec.collection],
                "upsert_attempted_count": inserted_counts[spec.collection],
                "db_row_count_after": db_counts[spec.collection],
            }
        )

    if report_file:
        report_file.parent.mkdir(parents=True, exist_ok=True)
        with report_file.open("w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=True)

    return report


def _print_report(report: Dict[str, Any]) -> None:
    print("=" * 80)
    print(f"Migration mode: {report['mode']}")
    print(f"Input dir: {report['input_dir']}")
    print("=" * 80)
    print("Collection reconciliation:")
    for item in report["collections"]:
        print(
            f"- {item['collection']}: source={item['source_count']} "
            f"valid={item['transformed_valid_count']} skipped={item['invalid_skipped_count']} "
            f"upserted={item['upsert_attempted_count']} db_after={item['db_row_count_after']}"
        )
    print("=" * 80)
    print("Integrity checks (expected 0):")
    for key, value in report["integrity_checks"].items():
        print(f"- {key}: {value}")
    print("=" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Legacy-exported JSON/NDJSON into PostgreSQL")
    parser.add_argument("--input-dir", required=True, help="Directory containing collection JSON/JSONL/NDJSON files")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--dry-run", action="store_true", help="Parse and validate only; no DB writes")
    mode_group.add_argument("--apply", action="store_true", help="Run upserts into PostgreSQL")
    parser.add_argument("--report-file", default="migration-reports/legacy_to_postgres_report.json", help="Path to write JSON report")
    parser.add_argument("--chunk-size", type=int, default=1000, help="Upsert chunk size")
    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    report_path = Path(args.report_file).resolve() if args.report_file else None
    report = run_migration(
        input_dir=input_dir,
        apply=bool(args.apply),
        report_file=report_path,
        chunk_size=int(args.chunk_size),
    )
    _print_report(report)
    if report_path:
        print(f"Report written to: {report_path}")


if __name__ == "__main__":
    main()
