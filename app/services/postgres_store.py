"""PostgreSQL-backed persistence adapter for core auth/org/platform domains."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.services.database import session_scope
from app.services.postgres_models import (
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
    ScheduledSendModel,
    SmsCampaignEnrollmentModel,
    SmsCampaignModel,
    SmsConversationModel,
    SmsLeadModel,
    SmsMessageModel,
    SmsSuppressionModel,
    SystemSettingModel,
    TenantModel,
    TwilioIntegrationModel,
    UserModel,
)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _user_to_dict(row: UserModel) -> Dict[str, Any]:
    return {
        "id": row.id,
        "email": row.email,
        "username": row.username,
        "hashed_password": row.hashed_password,
        "first_name": row.first_name,
        "last_name": row.last_name,
        "full_name": row.full_name,
        "role": row.role,
        "status": row.status,
        "is_active": row.is_active,
        "is_verified": row.is_verified,
        "is_email_verified": row.is_email_verified,
        "tenant_id": row.tenant_id,
        "allowed_app_ids": row.allowed_app_ids or [],
        "default_app_id": row.default_app_id,
        "platform_role_id": row.platform_role_id,
        "active_org_id": row.active_org_id,
        "last_login": row.last_login,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _org_to_dict(row: OrgModel) -> Dict[str, Any]:
    return {
        "id": row.id,
        "org_type": row.org_type,
        "parent_org_id": row.parent_org_id,
        "legacy_tenant_id": row.legacy_tenant_id,
        "name": row.name,
        "status": row.status,
        "branding": row.branding or {},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _membership_to_dict(row: OrgMembershipModel) -> Dict[str, Any]:
    return {
        "id": row.id,
        "org_id": row.org_id,
        "user_id": row.user_id,
        "role": row.role,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _role_to_dict(row: PlatformRoleModel) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "slug": row.slug,
        "scope": row.scope,
        "description": row.description,
        "permissions": row.permissions or [],
        "status": row.status,
        "is_system": row.is_system,
        "created_by_user_id": row.created_by_user_id,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _entitlement_to_dict(row: PartnerEntitlementModel) -> Dict[str, Any]:
    return {
        "partner_org_id": row.partner_org_id,
        "appointment_setter_enabled": bool(row.appointment_setter_enabled),
        "approved_by_user_id": row.approved_by_user_id,
        "approval_notes": row.approval_notes,
        "onboarding_status": row.onboarding_status,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _audit_to_dict(row: AuditLogModel) -> Dict[str, Any]:
    return {
        "id": row.id,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "status": row.status,
        "actor": row.actor or {},
        "metadata": row.metadata_json or {},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _idempotency_to_dict(row: IdempotencyKeyModel) -> Dict[str, Any]:
    return {
        "id": row.id,
        "scope": row.scope,
        "key": row.key,
        "request_hash": row.request_hash,
        "status": row.status,
        "user_id": row.user_id,
        "error_message": row.error_message,
        "response_payload": row.response_payload,
        "completed_at": _iso(row.completed_at),
        "failed_at": _iso(row.failed_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _merge_payload(data: Optional[Dict[str, Any]], fixed: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(data or {})
    payload.update({k: v for k, v in fixed.items() if v is not None})
    return payload


def _sms_lead_to_dict(row: SmsLeadModel) -> Dict[str, Any]:
    return _merge_payload(
        row.data,
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "phone_number": row.phone_number,
            "status": row.status,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        },
    )


def _sms_campaign_to_dict(row: SmsCampaignModel) -> Dict[str, Any]:
    return _merge_payload(
        row.data,
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "from_phone_number": row.from_phone_number,
            "status": row.status,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        },
    )


def _sms_enrollment_to_dict(row: SmsCampaignEnrollmentModel) -> Dict[str, Any]:
    return _merge_payload(
        row.data,
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "campaign_id": row.campaign_id,
            "lead_id": row.lead_id,
            "state": row.state,
            "current_step": row.current_step,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        },
    )


def _scheduled_send_to_dict(row: ScheduledSendModel) -> Dict[str, Any]:
    return _merge_payload(
        row.data,
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "campaign_id": row.campaign_id,
            "enrollment_id": row.enrollment_id,
            "lead_id": row.lead_id,
            "to_phone_number": row.to_phone_number,
            "step_index": row.step_index,
            "send_after": _iso(row.send_after),
            "status": row.status,
            "attempts": row.attempts,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        },
    )


def _sms_message_to_dict(row: SmsMessageModel) -> Dict[str, Any]:
    return _merge_payload(
        row.data,
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "campaign_id": row.campaign_id,
            "lead_id": row.lead_id,
            "direction": row.direction,
            "twilio_sid": row.twilio_sid,
            "status": row.status,
            "from_phone_number": row.from_phone_number,
            "to_phone_number": row.to_phone_number,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        },
    )


def _sms_conversation_to_dict(row: SmsConversationModel) -> Dict[str, Any]:
    return _merge_payload(
        row.data,
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "lead_id": row.lead_id,
            "lead_phone_number": row.lead_phone_number,
            "tenant_phone_number": row.tenant_phone_number,
            "control_mode": row.control_mode,
            "status": row.status,
            "last_message_at": _iso(row.last_message_at),
            "unread_count": row.unread_count,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        },
    )


def _sms_suppression_to_dict(row: SmsSuppressionModel) -> Dict[str, Any]:
    return _merge_payload(
        row.data,
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "phone_number": row.phone_number,
            "reason": row.reason,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        },
    )


class PostgresStore:
    """Data access for core entities with PostgreSQL-compatible dict responses."""

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _idempotency_doc_id(scope: str, key: str) -> str:
        return hashlib.sha256(f"{scope}:{key}".encode("utf-8")).hexdigest()

    async def create_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = UserModel(**user_data)
            session.add(row)
            session.flush()
            session.refresh(row)
            return _user_to_dict(row)

    async def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(UserModel).where(func.lower(UserModel.email) == str(email).strip().lower()))
            return _user_to_dict(row) if row else None

    async def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(UserModel, user_id)
            return _user_to_dict(row) if row else None

    async def list_users(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(UserModel).order_by(UserModel.created_at.desc()).offset(offset).limit(limit)).all()
            return [_user_to_dict(row) for row in rows]

    async def update_user(self, user_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = session.get(UserModel, user_id)
            if row is None:
                raise ValueError(f"user '{user_id}' not found")
            for key, value in update_data.items():
                setattr(row, key, value)
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _user_to_dict(row)

    async def delete_user(self, user_id: str) -> bool:
        with session_scope() as session:
            row = session.get(UserModel, user_id)
            if row is None:
                return False
            session.delete(row)
            return True

    async def create_org(self, org_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = OrgModel(**org_data)
            session.add(row)
            session.flush()
            session.refresh(row)
            return _org_to_dict(row)

    async def get_org(self, org_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(OrgModel, org_id)
            return _org_to_dict(row) if row else None

    async def list_orgs(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(OrgModel).order_by(OrgModel.created_at.asc()).offset(offset).limit(limit)).all()
            return [_org_to_dict(row) for row in rows]

    async def update_org(self, org_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(OrgModel, org_id)
            if row is None:
                return None
            for key, value in update_data.items():
                setattr(row, key, value)
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _org_to_dict(row)

    async def delete_org(self, org_id: str) -> bool:
        with session_scope() as session:
            row = session.get(OrgModel, org_id)
            if row is None:
                return False
            session.delete(row)
            return True

    async def get_org_by_legacy_tenant_id(
        self, legacy_tenant_id: str, prefer_customer: bool = False
    ) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(OrgModel).where(OrgModel.legacy_tenant_id == legacy_tenant_id)).all()
            if not rows:
                return None
            if not prefer_customer:
                return _org_to_dict(rows[0])
            customer = next((row for row in rows if str(row.org_type).lower() == "customer"), None)
            return _org_to_dict(customer or rows[0])

    async def list_descendant_orgs(self, org_id: str) -> List[Dict[str, Any]]:
        with session_scope() as session:
            queue = [org_id]
            descendants: List[OrgModel] = []
            seen: set[str] = set()
            while queue:
                parent = queue.pop(0)
                if parent in seen:
                    continue
                seen.add(parent)
                children = session.scalars(select(OrgModel).where(OrgModel.parent_org_id == parent)).all()
                descendants.extend(children)
                queue.extend([child.id for child in children if child.id and child.id not in seen])
            return [_org_to_dict(row) for row in descendants]

    async def upsert_org_membership(self, membership_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = session.get(OrgMembershipModel, membership_data["id"])
            if row is None:
                row = OrgMembershipModel(**membership_data)
                session.add(row)
            else:
                for key, value in membership_data.items():
                    setattr(row, key, value)
                row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _membership_to_dict(row)

    async def get_org_membership(self, org_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(
                select(OrgMembershipModel).where(
                    OrgMembershipModel.org_id == org_id,
                    OrgMembershipModel.user_id == user_id,
                )
            )
            return _membership_to_dict(row) if row else None

    async def list_org_memberships_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(OrgMembershipModel).where(OrgMembershipModel.user_id == user_id)).all()
            return [_membership_to_dict(row) for row in rows]

    async def list_org_memberships_for_org(self, org_id: str) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(OrgMembershipModel).where(OrgMembershipModel.org_id == org_id)).all()
            return [_membership_to_dict(row) for row in rows]

    async def delete_org_membership(self, org_id: str, user_id: str) -> bool:
        with session_scope() as session:
            row = session.scalar(
                select(OrgMembershipModel).where(
                    OrgMembershipModel.org_id == org_id,
                    OrgMembershipModel.user_id == user_id,
                )
            )
            if row is None:
                return False
            session.delete(row)
            return True

    async def get_partner_entitlements(self, partner_org_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(PartnerEntitlementModel, partner_org_id)
            return _entitlement_to_dict(row) if row else None

    async def upsert_partner_entitlements(self, partner_org_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = session.get(PartnerEntitlementModel, partner_org_id)
            if row is None:
                row = PartnerEntitlementModel(partner_org_id=partner_org_id, **payload)
                session.add(row)
            else:
                for key, value in payload.items():
                    setattr(row, key, value)
                row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _entitlement_to_dict(row)

    async def delete_partner_entitlements(self, partner_org_id: str) -> bool:
        with session_scope() as session:
            row = session.get(PartnerEntitlementModel, partner_org_id)
            if row is None:
                return False
            session.delete(row)
            return True

    async def create_platform_role(self, role_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = PlatformRoleModel(**role_data)
            session.add(row)
            session.flush()
            session.refresh(row)
            return _role_to_dict(row)

    async def get_platform_role(self, role_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(PlatformRoleModel, role_id)
            return _role_to_dict(row) if row else None

    async def get_platform_role_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(PlatformRoleModel).where(PlatformRoleModel.slug == slug))
            return _role_to_dict(row) if row else None

    async def list_platform_roles(self, limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(PlatformRoleModel).order_by(PlatformRoleModel.created_at.asc()).offset(offset).limit(limit)).all()
            return [_role_to_dict(row) for row in rows]

    async def update_platform_role(self, role_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(PlatformRoleModel, role_id)
            if row is None:
                return None
            for key, value in update_data.items():
                setattr(row, key, value)
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _role_to_dict(row)

    async def create_audit_log(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = AuditLogModel(**log_data)
            session.add(row)
            session.flush()
            session.refresh(row)
            return _audit_to_dict(row)

    async def list_audit_logs(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(AuditLogModel).order_by(AuditLogModel.created_at.desc()).offset(offset).limit(limit)).all()
            return [_audit_to_dict(row) for row in rows]

    async def acquire_idempotency_key(
        self,
        *,
        scope: str,
        key: str,
        request_hash: str,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        doc_id = self._idempotency_doc_id(scope=scope, key=key)
        with session_scope() as session:
            payload = IdempotencyKeyModel(
                id=doc_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
                status="in_progress",
                user_id=user_id,
                response_payload=None,
            )
            try:
                session.add(payload)
                session.flush()
                session.refresh(payload)
                return {"state": "acquired", "record": _idempotency_to_dict(payload)}
            except IntegrityError:
                session.rollback()
                row = session.get(IdempotencyKeyModel, doc_id)
                if row is None:
                    return {"state": "in_progress", "record": {}}
                record = _idempotency_to_dict(row)
                if record.get("request_hash") and record["request_hash"] != request_hash:
                    return {"state": "conflict", "record": record}
                state = str(record.get("status", "in_progress")).lower()
                if state == "completed":
                    return {"state": "completed", "record": record}
                if state == "failed":
                    return {"state": "failed", "record": record}
                return {"state": "in_progress", "record": record}

    async def complete_idempotency_key(
        self,
        *,
        scope: str,
        key: str,
        response_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        doc_id = self._idempotency_doc_id(scope=scope, key=key)
        with session_scope() as session:
            row = session.get(IdempotencyKeyModel, doc_id)
            if row is None:
                row = IdempotencyKeyModel(
                    id=doc_id,
                    scope=scope,
                    key=key,
                    request_hash="",
                    status="completed",
                    response_payload=response_payload,
                    completed_at=self._now(),
                )
                session.add(row)
            else:
                row.status = "completed"
                row.response_payload = response_payload
                row.completed_at = self._now()
                row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _idempotency_to_dict(row)

    async def fail_idempotency_key(
        self,
        *,
        scope: str,
        key: str,
        error_message: str,
    ) -> Dict[str, Any]:
        doc_id = self._idempotency_doc_id(scope=scope, key=key)
        with session_scope() as session:
            row = session.get(IdempotencyKeyModel, doc_id)
            if row is None:
                row = IdempotencyKeyModel(
                    id=doc_id,
                    scope=scope,
                    key=key,
                    request_hash="",
                    status="failed",
                    error_message=(error_message or "")[:500],
                    failed_at=self._now(),
                )
                session.add(row)
            else:
                row.status = "failed"
                row.error_message = (error_message or "")[:500]
                row.failed_at = self._now()
                row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _idempotency_to_dict(row)

    async def health_check(self) -> bool:
        return True

    async def create_tenant(self, tenant_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = TenantModel(
                id=tenant_data["id"],
                name=tenant_data.get("name", ""),
                name_lower=tenant_data.get("name_lower"),
                timezone=tenant_data.get("timezone"),
                status=tenant_data.get("status"),
                data=dict(tenant_data),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "name": row.name, "name_lower": row.name_lower, "timezone": row.timezone, "status": row.status, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(TenantModel, tenant_id)
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "name": row.name, "name_lower": row.name_lower, "timezone": row.timezone, "status": row.status, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def update_tenant(self, tenant_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = session.get(TenantModel, tenant_id)
            if row is None:
                raise ValueError(f"tenant '{tenant_id}' not found")
            merged = dict(row.data or {})
            merged.update(update_data)
            row.data = merged
            if "name" in update_data:
                row.name = str(update_data.get("name") or "")
            if "name_lower" in update_data:
                row.name_lower = update_data.get("name_lower")
            if "timezone" in update_data:
                row.timezone = update_data.get("timezone")
            if "status" in update_data:
                row.status = update_data.get("status")
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "name": row.name, "name_lower": row.name_lower, "timezone": row.timezone, "status": row.status, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def list_tenants(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(TenantModel).order_by(TenantModel.created_at.asc()).offset(offset).limit(limit)).all()
            return [
                _merge_payload(row.data, {"id": row.id, "name": row.name, "name_lower": row.name_lower, "timezone": row.timezone, "status": row.status, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})
                for row in rows
            ]

    async def get_tenant_by_name_lower(self, name_lower: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(TenantModel).where(TenantModel.name_lower == name_lower).limit(1))
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "name": row.name, "name_lower": row.name_lower, "timezone": row.timezone, "status": row.status, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def create_business_config(self, business_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = BusinessConfigModel(
                id=business_data["id"],
                tenant_id=business_data.get("tenant_id", ""),
                data=dict(business_data),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def get_business_config(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(BusinessConfigModel).where(BusinessConfigModel.tenant_id == tenant_id).limit(1))
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def update_business_config(self, tenant_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(BusinessConfigModel).where(BusinessConfigModel.tenant_id == tenant_id).limit(1))
            if row is None:
                return None
            merged = dict(row.data or {})
            merged.update(update_data)
            row.data = merged
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def create_agent_settings(self, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = AgentSettingsModel(
                id=agent_data["id"],
                tenant_id=agent_data.get("tenant_id", ""),
                data=dict(agent_data),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def get_agent_settings(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(AgentSettingsModel).where(AgentSettingsModel.tenant_id == tenant_id).limit(1))
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def update_agent_settings(self, tenant_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(AgentSettingsModel).where(AgentSettingsModel.tenant_id == tenant_id).limit(1))
            if row is None:
                return None
            merged = dict(row.data or {})
            merged.update(update_data)
            row.data = merged
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def create_twilio_integration(self, twilio_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = TwilioIntegrationModel(
                id=twilio_data["id"],
                tenant_id=twilio_data.get("tenant_id", ""),
                data=dict(twilio_data),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def get_twilio_integration(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(TwilioIntegrationModel).where(TwilioIntegrationModel.tenant_id == tenant_id).limit(1))
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def update_twilio_integration(self, tenant_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(TwilioIntegrationModel).where(TwilioIntegrationModel.tenant_id == tenant_id).limit(1))
            if row is None:
                return None
            merged = dict(row.data or {})
            merged.update(update_data)
            row.data = merged
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def delete_twilio_integration(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(TwilioIntegrationModel).where(TwilioIntegrationModel.tenant_id == tenant_id).limit(1))
            if row is None:
                return None
            payload = _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})
            session.delete(row)
            return payload

    async def create_agent(self, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = AgentDomainModel(
                id=agent_data["id"],
                tenant_id=agent_data.get("tenant_id", ""),
                data=dict(agent_data),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(AgentDomainModel, agent_id)
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def list_agents_by_tenant(self, tenant_id: str) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(AgentDomainModel).where(AgentDomainModel.tenant_id == tenant_id).order_by(AgentDomainModel.created_at.asc())).all()
            return [_merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}) for row in rows]

    async def update_agent(self, agent_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = session.get(AgentDomainModel, agent_id)
            if row is None:
                raise ValueError(f"agent '{agent_id}' not found")
            merged = dict(row.data or {})
            merged.update(update_data)
            row.data = merged
            if "tenant_id" in update_data:
                row.tenant_id = str(update_data.get("tenant_id") or row.tenant_id)
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def delete_agent(self, agent_id: str) -> bool:
        with session_scope() as session:
            row = session.get(AgentDomainModel, agent_id)
            if row is None:
                return False
            session.delete(row)
            return True

    async def create_phone_number(self, phone_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = PhoneNumberModel(
                id=phone_data["id"],
                tenant_id=phone_data.get("tenant_id", ""),
                agent_id=phone_data.get("agent_id"),
                phone_number=phone_data.get("phone_number", ""),
                data=dict(phone_data),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "agent_id": row.agent_id, "phone_number": row.phone_number, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def get_phone_number(self, phone_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(PhoneNumberModel, phone_id)
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "agent_id": row.agent_id, "phone_number": row.phone_number, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def get_phone_by_number(self, phone_number: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(PhoneNumberModel).where(PhoneNumberModel.phone_number == phone_number).limit(1))
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "agent_id": row.agent_id, "phone_number": row.phone_number, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def get_phone_by_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(PhoneNumberModel).where(PhoneNumberModel.agent_id == agent_id).order_by(PhoneNumberModel.created_at.asc()).limit(1))
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "agent_id": row.agent_id, "phone_number": row.phone_number, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def list_phones_by_tenant(self, tenant_id: str) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(PhoneNumberModel).where(PhoneNumberModel.tenant_id == tenant_id).order_by(PhoneNumberModel.created_at.asc())).all()
            return [_merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "agent_id": row.agent_id, "phone_number": row.phone_number, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}) for row in rows]

    async def update_phone_number(self, phone_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = session.get(PhoneNumberModel, phone_id)
            if row is None:
                raise ValueError(f"phone '{phone_id}' not found")
            merged = dict(row.data or {})
            merged.update(update_data)
            row.data = merged
            if "tenant_id" in update_data:
                row.tenant_id = str(update_data.get("tenant_id") or row.tenant_id)
            if "agent_id" in update_data:
                row.agent_id = update_data.get("agent_id")
            if "phone_number" in update_data and update_data.get("phone_number"):
                row.phone_number = str(update_data.get("phone_number"))
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "agent_id": row.agent_id, "phone_number": row.phone_number, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def delete_phone_number(self, phone_id: str) -> bool:
        with session_scope() as session:
            row = session.get(PhoneNumberModel, phone_id)
            if row is None:
                return False
            session.delete(row)
            return True

    async def create_appointment(self, appointment_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = AppointmentDomainModel(
                id=appointment_data["id"],
                tenant_id=appointment_data.get("tenant_id", ""),
                status=appointment_data.get("status"),
                appointment_datetime=appointment_data.get("appointment_datetime"),
                data=dict(appointment_data),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "status": row.status, "appointment_datetime": row.appointment_datetime, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def get_appointment(self, appointment_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(AppointmentDomainModel, appointment_id)
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "status": row.status, "appointment_datetime": row.appointment_datetime, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def list_appointments(self, tenant_id: str, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(AppointmentDomainModel).where(AppointmentDomainModel.tenant_id == tenant_id).order_by(AppointmentDomainModel.created_at.asc()).offset(offset).limit(limit)).all()
            return [_merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "status": row.status, "appointment_datetime": row.appointment_datetime, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}) for row in rows]

    async def list_appointments_by_date_range(
        self, tenant_id: str, start_date: str, end_date: str, statuses: Optional[List[str]] = None, limit: int = 1000
    ) -> List[Dict[str, Any]]:
        with session_scope() as session:
            stmt = select(AppointmentDomainModel).where(
                AppointmentDomainModel.tenant_id == tenant_id,
                AppointmentDomainModel.appointment_datetime >= start_date,
                AppointmentDomainModel.appointment_datetime <= end_date,
            )
            if statuses:
                stmt = stmt.where(AppointmentDomainModel.status.in_(statuses))
            rows = session.scalars(stmt.order_by(AppointmentDomainModel.appointment_datetime.asc()).limit(limit)).all()
            return [_merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "status": row.status, "appointment_datetime": row.appointment_datetime, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}) for row in rows]

    async def update_appointment(self, appointment_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = session.get(AppointmentDomainModel, appointment_id)
            if row is None:
                raise ValueError(f"appointment '{appointment_id}' not found")
            merged = dict(row.data or {})
            merged.update(update_data)
            row.data = merged
            if "tenant_id" in update_data:
                row.tenant_id = str(update_data.get("tenant_id") or row.tenant_id)
            if "status" in update_data:
                row.status = update_data.get("status")
            if "appointment_datetime" in update_data:
                row.appointment_datetime = update_data.get("appointment_datetime")
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "status": row.status, "appointment_datetime": row.appointment_datetime, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def create_provisioning_job(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = ProvisioningJobModel(
                id=job_data["id"],
                tenant_id=job_data.get("tenant_id"),
                status=job_data.get("status"),
                data=dict(job_data),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "status": row.status, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def get_provisioning_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(ProvisioningJobModel, job_id)
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "status": row.status, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def update_provisioning_job(self, job_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = session.get(ProvisioningJobModel, job_id)
            if row is None:
                raise ValueError(f"provisioning job '{job_id}' not found")
            merged = dict(row.data or {})
            merged.update(update_data)
            row.data = merged
            if "tenant_id" in update_data:
                row.tenant_id = update_data.get("tenant_id")
            if "status" in update_data:
                row.status = update_data.get("status")
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "status": row.status, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def create_contact(self, contact_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = ContactModel(
                id=contact_data["id"],
                tenant_id=contact_data.get("tenant_id"),
                data=dict(contact_data),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "tenant_id": row.tenant_id, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def create_chatbot_agent(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = ChatbotAgentModel(
                id=payload["id"],
                owner_user_id=payload.get("owner_user_id", ""),
                status=payload.get("status"),
                embed_token_version=int(payload.get("embed_token_version", 1)),
                data=dict(payload),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "owner_user_id": row.owner_user_id, "status": row.status, "embed_token_version": row.embed_token_version, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def get_chatbot_agent(self, chatbot_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(ChatbotAgentModel, chatbot_id)
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "owner_user_id": row.owner_user_id, "status": row.status, "embed_token_version": row.embed_token_version, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def list_chatbot_agents_by_owner(self, owner_user_id: str, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(ChatbotAgentModel).where(ChatbotAgentModel.owner_user_id == owner_user_id).order_by(ChatbotAgentModel.created_at.desc()).offset(offset).limit(limit)).all()
            return [_merge_payload(row.data, {"id": row.id, "owner_user_id": row.owner_user_id, "status": row.status, "embed_token_version": row.embed_token_version, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}) for row in rows]

    async def list_chatbot_agents(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(ChatbotAgentModel).order_by(ChatbotAgentModel.created_at.desc()).offset(offset).limit(limit)).all()
            return [_merge_payload(row.data, {"id": row.id, "owner_user_id": row.owner_user_id, "status": row.status, "embed_token_version": row.embed_token_version, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}) for row in rows]

    async def update_chatbot_agent(self, chatbot_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(ChatbotAgentModel, chatbot_id)
            if row is None:
                return None
            merged = dict(row.data or {})
            merged.update(payload)
            row.data = merged
            if "owner_user_id" in payload:
                row.owner_user_id = str(payload.get("owner_user_id") or row.owner_user_id)
            if "status" in payload:
                row.status = payload.get("status")
            if "embed_token_version" in payload:
                row.embed_token_version = int(payload.get("embed_token_version") or row.embed_token_version)
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "owner_user_id": row.owner_user_id, "status": row.status, "embed_token_version": row.embed_token_version, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def delete_chatbot_agent(self, chatbot_id: str) -> bool:
        with session_scope() as session:
            row = session.get(ChatbotAgentModel, chatbot_id)
            if row is None:
                return False
            session.delete(row)
            return True

    async def create_chatbot_runtime_log(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            import uuid

            row = ChatbotRuntimeLogModel(
                id=log_data.get("id") or str(uuid.uuid4()),
                chatbot_id=log_data.get("chatbot_id", ""),
                status=log_data.get("status"),
                timestamp=log_data.get("timestamp"),
                data=dict(log_data),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "chatbot_id": row.chatbot_id, "status": row.status, "timestamp": row.timestamp, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def list_chatbot_runtime_logs(
        self, chatbot_id: str, limit: int = 100, status_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        with session_scope() as session:
            stmt = select(ChatbotRuntimeLogModel).where(ChatbotRuntimeLogModel.chatbot_id == chatbot_id)
            if status_filter:
                stmt = stmt.where(ChatbotRuntimeLogModel.status == status_filter)
            rows = session.scalars(stmt.order_by(ChatbotRuntimeLogModel.created_at.desc()).limit(limit)).all()
            return [_merge_payload(row.data, {"id": row.id, "chatbot_id": row.chatbot_id, "status": row.status, "timestamp": row.timestamp, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}) for row in rows]

    async def get_chatbot_runtime_settings(self) -> Dict[str, Any]:
        with session_scope() as session:
            row = session.get(SystemSettingModel, "chatbot_runtime")
            if row is None:
                return {"enabled": True}
            return dict(row.data or {})

    async def update_chatbot_runtime_settings(self, enabled: bool, updated_by: str) -> Dict[str, Any]:
        with session_scope() as session:
            row = session.get(SystemSettingModel, "chatbot_runtime")
            payload = {"enabled": bool(enabled), "updated_by": updated_by, "updated_at": self._now().isoformat()}
            if row is None:
                row = SystemSettingModel(key="chatbot_runtime", data=payload)
                session.add(row)
            else:
                merged = dict(row.data or {})
                merged.update(payload)
                row.data = merged
                row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return dict(row.data or {})

    async def create_chatbot_chat_session(self, session_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = ChatbotChatSessionModel(
                id=session_data["id"],
                chatbot_id=session_data.get("chatbot_id", ""),
                owner_user_id=session_data.get("owner_user_id"),
                visitor_session_id=session_data.get("visitor_session_id"),
                origin=session_data.get("origin"),
                status=session_data.get("status"),
                control_mode=session_data.get("control_mode"),
                assigned_operator_id=session_data.get("assigned_operator_id"),
                assigned_operator_name=session_data.get("assigned_operator_name"),
                data=dict(session_data),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "chatbot_id": row.chatbot_id, "owner_user_id": row.owner_user_id, "visitor_session_id": row.visitor_session_id, "origin": row.origin, "status": row.status, "control_mode": row.control_mode, "assigned_operator_id": row.assigned_operator_id, "assigned_operator_name": row.assigned_operator_name, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def get_chatbot_chat_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(ChatbotChatSessionModel, session_id)
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "chatbot_id": row.chatbot_id, "owner_user_id": row.owner_user_id, "visitor_session_id": row.visitor_session_id, "origin": row.origin, "status": row.status, "control_mode": row.control_mode, "assigned_operator_id": row.assigned_operator_id, "assigned_operator_name": row.assigned_operator_name, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def update_chatbot_chat_session(self, session_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(ChatbotChatSessionModel, session_id)
            if row is None:
                return None
            merged = dict(row.data or {})
            merged.update(payload)
            row.data = merged
            if "chatbot_id" in payload:
                row.chatbot_id = str(payload.get("chatbot_id") or row.chatbot_id)
            if "owner_user_id" in payload:
                row.owner_user_id = payload.get("owner_user_id")
            if "visitor_session_id" in payload:
                row.visitor_session_id = payload.get("visitor_session_id")
            if "origin" in payload:
                row.origin = payload.get("origin")
            if "status" in payload:
                row.status = payload.get("status")
            if "control_mode" in payload:
                row.control_mode = payload.get("control_mode")
            if "assigned_operator_id" in payload:
                row.assigned_operator_id = payload.get("assigned_operator_id")
            if "assigned_operator_name" in payload:
                row.assigned_operator_name = payload.get("assigned_operator_name")
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "chatbot_id": row.chatbot_id, "owner_user_id": row.owner_user_id, "visitor_session_id": row.visitor_session_id, "origin": row.origin, "status": row.status, "control_mode": row.control_mode, "assigned_operator_id": row.assigned_operator_id, "assigned_operator_name": row.assigned_operator_name, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def find_open_chatbot_chat_session(self, chatbot_id: str, visitor_session_id: str, origin: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(
                select(ChatbotChatSessionModel).where(
                    ChatbotChatSessionModel.chatbot_id == chatbot_id,
                    ChatbotChatSessionModel.visitor_session_id == visitor_session_id,
                    ChatbotChatSessionModel.origin == origin,
                    ChatbotChatSessionModel.status == "open",
                ).order_by(ChatbotChatSessionModel.created_at.desc()).limit(1)
            )
            if row is None:
                return None
            return _merge_payload(row.data, {"id": row.id, "chatbot_id": row.chatbot_id, "owner_user_id": row.owner_user_id, "visitor_session_id": row.visitor_session_id, "origin": row.origin, "status": row.status, "control_mode": row.control_mode, "assigned_operator_id": row.assigned_operator_id, "assigned_operator_name": row.assigned_operator_name, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def list_chatbot_chat_sessions_by_owner(self, owner_user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(ChatbotChatSessionModel).where(ChatbotChatSessionModel.owner_user_id == owner_user_id).order_by(ChatbotChatSessionModel.created_at.desc()).limit(limit)).all()
            return [_merge_payload(row.data, {"id": row.id, "chatbot_id": row.chatbot_id, "owner_user_id": row.owner_user_id, "visitor_session_id": row.visitor_session_id, "origin": row.origin, "status": row.status, "control_mode": row.control_mode, "assigned_operator_id": row.assigned_operator_id, "assigned_operator_name": row.assigned_operator_name, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}) for row in rows]

    async def list_chatbot_chat_sessions(self, limit: int = 100) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(ChatbotChatSessionModel).order_by(ChatbotChatSessionModel.created_at.desc()).limit(limit)).all()
            return [_merge_payload(row.data, {"id": row.id, "chatbot_id": row.chatbot_id, "owner_user_id": row.owner_user_id, "visitor_session_id": row.visitor_session_id, "origin": row.origin, "status": row.status, "control_mode": row.control_mode, "assigned_operator_id": row.assigned_operator_id, "assigned_operator_name": row.assigned_operator_name, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}) for row in rows]

    async def count_chatbot_chat_sessions_for_visitor(self, chatbot_id: str, visitor_session_id: str, origin: str) -> int:
        with session_scope() as session:
            count = session.scalar(
                select(func.count()).select_from(ChatbotChatSessionModel).where(
                    ChatbotChatSessionModel.chatbot_id == chatbot_id,
                    ChatbotChatSessionModel.visitor_session_id == visitor_session_id,
                    ChatbotChatSessionModel.origin == origin,
                )
            )
            return int(count or 0)

    async def create_chatbot_chat_message(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = ChatbotChatMessageModel(
                id=message_data["id"],
                session_id=message_data.get("session_id", ""),
                sender_type=message_data.get("sender_type"),
                sender_id=message_data.get("sender_id"),
                role=message_data.get("role"),
                content=message_data.get("content"),
                data=dict(message_data),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _merge_payload(row.data, {"id": row.id, "session_id": row.session_id, "sender_type": row.sender_type, "sender_id": row.sender_id, "role": row.role, "content": row.content, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)})

    async def list_chatbot_chat_messages(self, session_id: str, limit: int = 250) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(select(ChatbotChatMessageModel).where(ChatbotChatMessageModel.session_id == session_id).order_by(ChatbotChatMessageModel.created_at.asc()).limit(limit)).all()
            return [_merge_payload(row.data, {"id": row.id, "session_id": row.session_id, "sender_type": row.sender_type, "sender_id": row.sender_id, "role": row.role, "content": row.content, "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at)}) for row in rows]

    # ------------------------------------------------------------------
    # SMS App: leads
    # ------------------------------------------------------------------

    async def upsert_sms_lead(self, lead_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create or update a lead, deduped on (tenant_id, phone_number). Idempotent re-import."""
        tenant_id = str(lead_data.get("tenant_id") or "")
        phone_number = str(lead_data.get("phone_number") or "")
        with session_scope() as session:
            row = session.scalar(
                select(SmsLeadModel)
                .where(SmsLeadModel.tenant_id == tenant_id, SmsLeadModel.phone_number == phone_number)
                .limit(1)
            )
            if row is None:
                row = SmsLeadModel(
                    id=lead_data["id"],
                    tenant_id=tenant_id,
                    phone_number=phone_number,
                    status=str(lead_data.get("status") or "new"),
                    data=dict(lead_data),
                )
                session.add(row)
            else:
                merged = dict(row.data or {})
                merged.update(lead_data)
                row.data = merged
                if lead_data.get("status"):
                    row.status = str(lead_data["status"])
                row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _sms_lead_to_dict(row)

    async def get_sms_lead(self, lead_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(SmsLeadModel, lead_id)
            return _sms_lead_to_dict(row) if row else None

    async def get_sms_lead_by_phone(self, tenant_id: str, phone_number: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(
                select(SmsLeadModel)
                .where(SmsLeadModel.tenant_id == tenant_id, SmsLeadModel.phone_number == phone_number)
                .limit(1)
            )
            return _sms_lead_to_dict(row) if row else None

    async def list_sms_leads(self, tenant_id: str, limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(SmsLeadModel)
                .where(SmsLeadModel.tenant_id == tenant_id)
                .order_by(SmsLeadModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [_sms_lead_to_dict(row) for row in rows]

    async def update_sms_lead(self, lead_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(SmsLeadModel, lead_id)
            if row is None:
                return None
            merged = dict(row.data or {})
            merged.update(update_data)
            row.data = merged
            if "status" in update_data and update_data.get("status"):
                row.status = str(update_data["status"])
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _sms_lead_to_dict(row)

    # ------------------------------------------------------------------
    # SMS App: campaigns
    # ------------------------------------------------------------------

    async def create_sms_campaign(self, campaign_data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as session:
            row = SmsCampaignModel(
                id=campaign_data["id"],
                tenant_id=str(campaign_data.get("tenant_id") or ""),
                from_phone_number=campaign_data.get("from_phone_number"),
                status=str(campaign_data.get("status") or "draft"),
                data=dict(campaign_data),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _sms_campaign_to_dict(row)

    async def get_sms_campaign(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(SmsCampaignModel, campaign_id)
            return _sms_campaign_to_dict(row) if row else None

    async def list_sms_campaigns(self, tenant_id: str) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(SmsCampaignModel)
                .where(SmsCampaignModel.tenant_id == tenant_id)
                .order_by(SmsCampaignModel.created_at.desc())
            ).all()
            return [_sms_campaign_to_dict(row) for row in rows]

    async def update_sms_campaign(self, campaign_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(SmsCampaignModel, campaign_id)
            if row is None:
                return None
            merged = dict(row.data or {})
            merged.update(update_data)
            row.data = merged
            if "status" in update_data and update_data.get("status"):
                row.status = str(update_data["status"])
            if "from_phone_number" in update_data:
                row.from_phone_number = update_data.get("from_phone_number")
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _sms_campaign_to_dict(row)

    # ------------------------------------------------------------------
    # SMS App: enrollments
    # ------------------------------------------------------------------

    async def upsert_sms_enrollment(self, enrollment_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create an enrollment, idempotent on (campaign_id, lead_id)."""
        campaign_id = str(enrollment_data.get("campaign_id") or "")
        lead_id = str(enrollment_data.get("lead_id") or "")
        with session_scope() as session:
            row = session.scalar(
                select(SmsCampaignEnrollmentModel)
                .where(
                    SmsCampaignEnrollmentModel.campaign_id == campaign_id,
                    SmsCampaignEnrollmentModel.lead_id == lead_id,
                )
                .limit(1)
            )
            if row is None:
                row = SmsCampaignEnrollmentModel(
                    id=enrollment_data["id"],
                    tenant_id=str(enrollment_data.get("tenant_id") or ""),
                    campaign_id=campaign_id,
                    lead_id=lead_id,
                    state=str(enrollment_data.get("state") or "pending"),
                    current_step=int(enrollment_data.get("current_step") or 0),
                    data=dict(enrollment_data),
                )
                session.add(row)
                try:
                    session.flush()
                except IntegrityError:
                    # Lost a race to another concurrent enrollment; fetch the winner.
                    session.rollback()
                    row = session.scalar(
                        select(SmsCampaignEnrollmentModel)
                        .where(
                            SmsCampaignEnrollmentModel.campaign_id == campaign_id,
                            SmsCampaignEnrollmentModel.lead_id == lead_id,
                        )
                        .limit(1)
                    )
            session.refresh(row)
            return _sms_enrollment_to_dict(row)

    async def get_sms_enrollment(self, enrollment_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(SmsCampaignEnrollmentModel, enrollment_id)
            return _sms_enrollment_to_dict(row) if row else None

    async def list_sms_enrollments_for_lead(self, tenant_id: str, lead_id: str) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(SmsCampaignEnrollmentModel).where(
                    SmsCampaignEnrollmentModel.tenant_id == tenant_id,
                    SmsCampaignEnrollmentModel.lead_id == lead_id,
                )
            ).all()
            return [_sms_enrollment_to_dict(row) for row in rows]

    async def update_sms_enrollment(self, enrollment_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(SmsCampaignEnrollmentModel, enrollment_id)
            if row is None:
                return None
            merged = dict(row.data or {})
            merged.update(update_data)
            row.data = merged
            if "state" in update_data and update_data.get("state"):
                row.state = str(update_data["state"])
            if "current_step" in update_data and update_data.get("current_step") is not None:
                row.current_step = int(update_data["current_step"])
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _sms_enrollment_to_dict(row)

    # ------------------------------------------------------------------
    # SMS App: scheduled sends (the durable drip outbox)
    # ------------------------------------------------------------------

    async def create_scheduled_send(self, send_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a scheduled send. Idempotent on (enrollment_id, step_index)."""
        with session_scope() as session:
            row = ScheduledSendModel(
                id=send_data["id"],
                tenant_id=str(send_data.get("tenant_id") or ""),
                campaign_id=str(send_data.get("campaign_id") or ""),
                enrollment_id=str(send_data.get("enrollment_id") or ""),
                lead_id=str(send_data.get("lead_id") or ""),
                to_phone_number=str(send_data.get("to_phone_number") or ""),
                step_index=int(send_data.get("step_index") or 0),
                send_after=send_data["send_after"],
                status=str(send_data.get("status") or "scheduled"),
                attempts=int(send_data.get("attempts") or 0),
                data=dict(send_data),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                # Step already scheduled for this enrollment — idempotent no-op.
                session.rollback()
                return None
            session.refresh(row)
            return _scheduled_send_to_dict(row)

    async def claim_due_scheduled_sends(self, limit: int = 50, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Atomically claim due rows using SELECT ... FOR UPDATE SKIP LOCKED.

        Marks claimed rows as ``claimed`` inside the same transaction so concurrent
        workers never grab the same row (no double-send).
        """
        cutoff = now or self._now()
        with session_scope() as session:
            rows = session.scalars(
                select(ScheduledSendModel)
                .where(ScheduledSendModel.status == "scheduled", ScheduledSendModel.send_after <= cutoff)
                .order_by(ScheduledSendModel.send_after.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            claimed: List[Dict[str, Any]] = []
            for row in rows:
                row.status = "claimed"
                row.updated_at = self._now()
                claimed.append(_scheduled_send_to_dict(row))
            session.flush()
            return claimed

    async def update_scheduled_send(self, send_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(ScheduledSendModel, send_id)
            if row is None:
                return None
            merged = dict(row.data or {})
            merged.update({k: v for k, v in update_data.items() if k != "send_after"})
            row.data = merged
            if "status" in update_data and update_data.get("status"):
                row.status = str(update_data["status"])
            if "attempts" in update_data and update_data.get("attempts") is not None:
                row.attempts = int(update_data["attempts"])
            if "send_after" in update_data and update_data.get("send_after") is not None:
                row.send_after = update_data["send_after"]
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _scheduled_send_to_dict(row)

    async def cancel_pending_sends_for_lead(self, tenant_id: str, lead_id: str) -> int:
        """Cancel a lead's not-yet-sent scheduled rows (on reply or opt-out). Returns count."""
        with session_scope() as session:
            rows = session.scalars(
                select(ScheduledSendModel).where(
                    ScheduledSendModel.tenant_id == tenant_id,
                    ScheduledSendModel.lead_id == lead_id,
                    ScheduledSendModel.status.in_(["scheduled", "claimed"]),
                )
            ).all()
            count = 0
            for row in rows:
                row.status = "canceled"
                row.updated_at = self._now()
                count += 1
            session.flush()
            return count

    # ------------------------------------------------------------------
    # SMS App: messages
    # ------------------------------------------------------------------

    async def create_sms_message(self, message_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Record a message. Idempotent on twilio_sid (returns None on duplicate)."""
        with session_scope() as session:
            row = SmsMessageModel(
                id=message_data["id"],
                tenant_id=str(message_data.get("tenant_id") or ""),
                campaign_id=message_data.get("campaign_id"),
                lead_id=message_data.get("lead_id"),
                direction=str(message_data.get("direction") or "outbound"),
                twilio_sid=message_data.get("twilio_sid"),
                status=message_data.get("status"),
                from_phone_number=message_data.get("from_phone_number"),
                to_phone_number=message_data.get("to_phone_number"),
                data=dict(message_data),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                # Duplicate twilio_sid — webhook/status replay. Idempotent no-op.
                session.rollback()
                return None
            session.refresh(row)
            return _sms_message_to_dict(row)

    async def get_sms_message_by_sid(self, twilio_sid: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(SmsMessageModel).where(SmsMessageModel.twilio_sid == twilio_sid).limit(1))
            return _sms_message_to_dict(row) if row else None

    async def update_sms_message_status_by_sid(
        self, twilio_sid: str, status: str, extra: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.scalar(select(SmsMessageModel).where(SmsMessageModel.twilio_sid == twilio_sid).limit(1))
            if row is None:
                return None
            row.status = status
            merged = dict(row.data or {})
            merged["status"] = status
            if extra:
                merged.update(extra)
            row.data = merged
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _sms_message_to_dict(row)

    async def list_sms_messages_for_lead(self, tenant_id: str, lead_id: str, limit: int = 250) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(SmsMessageModel)
                .where(SmsMessageModel.tenant_id == tenant_id, SmsMessageModel.lead_id == lead_id)
                .order_by(SmsMessageModel.created_at.asc())
                .limit(limit)
            ).all()
            return [_sms_message_to_dict(row) for row in rows]

    # ------------------------------------------------------------------
    # SMS App: conversations (inbox)
    # ------------------------------------------------------------------

    async def upsert_sms_conversation(self, convo_data: Dict[str, Any]) -> Dict[str, Any]:
        """Get-or-create a conversation thread, keyed on (tenant, lead#, tenant#)."""
        tenant_id = str(convo_data.get("tenant_id") or "")
        lead_phone = str(convo_data.get("lead_phone_number") or "")
        tenant_phone = str(convo_data.get("tenant_phone_number") or "")
        with session_scope() as session:
            row = session.scalar(
                select(SmsConversationModel)
                .where(
                    SmsConversationModel.tenant_id == tenant_id,
                    SmsConversationModel.lead_phone_number == lead_phone,
                    SmsConversationModel.tenant_phone_number == tenant_phone,
                )
                .limit(1)
            )
            if row is None:
                row = SmsConversationModel(
                    id=convo_data["id"],
                    tenant_id=tenant_id,
                    lead_id=convo_data.get("lead_id"),
                    lead_phone_number=lead_phone,
                    tenant_phone_number=tenant_phone,
                    control_mode=str(convo_data.get("control_mode") or "automated"),
                    status=str(convo_data.get("status") or "open"),
                    data=dict(convo_data),
                )
                session.add(row)
                try:
                    session.flush()
                except IntegrityError:
                    session.rollback()
                    row = session.scalar(
                        select(SmsConversationModel)
                        .where(
                            SmsConversationModel.tenant_id == tenant_id,
                            SmsConversationModel.lead_phone_number == lead_phone,
                            SmsConversationModel.tenant_phone_number == tenant_phone,
                        )
                        .limit(1)
                    )
            session.refresh(row)
            return _sms_conversation_to_dict(row)

    async def get_sms_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(SmsConversationModel, conversation_id)
            return _sms_conversation_to_dict(row) if row else None

    async def list_sms_conversations(
        self, tenant_id: str, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(SmsConversationModel)
                .where(SmsConversationModel.tenant_id == tenant_id)
                .order_by(SmsConversationModel.last_message_at.desc().nullslast())
                .offset(offset)
                .limit(limit)
            ).all()
            return [_sms_conversation_to_dict(row) for row in rows]

    async def update_sms_conversation(
        self, conversation_id: str, update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        with session_scope() as session:
            row = session.get(SmsConversationModel, conversation_id)
            if row is None:
                return None
            merged = dict(row.data or {})
            merged.update({k: v for k, v in update_data.items() if k not in {"last_message_at", "unread_count"}})
            row.data = merged
            if "control_mode" in update_data and update_data.get("control_mode"):
                row.control_mode = str(update_data["control_mode"])
            if "status" in update_data and update_data.get("status"):
                row.status = str(update_data["status"])
            if "last_message_at" in update_data and update_data.get("last_message_at") is not None:
                row.last_message_at = update_data["last_message_at"]
            if "unread_count" in update_data and update_data.get("unread_count") is not None:
                row.unread_count = int(update_data["unread_count"])
            row.updated_at = self._now()
            session.flush()
            session.refresh(row)
            return _sms_conversation_to_dict(row)

    # ------------------------------------------------------------------
    # SMS App: suppressions (opt-out / do-not-contact)
    # ------------------------------------------------------------------

    async def add_sms_suppression(self, suppression_data: Dict[str, Any]) -> Dict[str, Any]:
        """Add to suppression list, idempotent on (tenant_id, phone_number)."""
        tenant_id = str(suppression_data.get("tenant_id") or "")
        phone_number = str(suppression_data.get("phone_number") or "")
        with session_scope() as session:
            row = session.scalar(
                select(SmsSuppressionModel)
                .where(
                    SmsSuppressionModel.tenant_id == tenant_id,
                    SmsSuppressionModel.phone_number == phone_number,
                )
                .limit(1)
            )
            if row is None:
                row = SmsSuppressionModel(
                    id=suppression_data["id"],
                    tenant_id=tenant_id,
                    phone_number=phone_number,
                    reason=str(suppression_data.get("reason") or "opted_out"),
                    data=dict(suppression_data),
                )
                session.add(row)
                try:
                    session.flush()
                except IntegrityError:
                    session.rollback()
                    row = session.scalar(
                        select(SmsSuppressionModel)
                        .where(
                            SmsSuppressionModel.tenant_id == tenant_id,
                            SmsSuppressionModel.phone_number == phone_number,
                        )
                        .limit(1)
                    )
            session.refresh(row)
            return _sms_suppression_to_dict(row)

    async def is_sms_suppressed(self, tenant_id: str, phone_number: str) -> bool:
        with session_scope() as session:
            row = session.scalar(
                select(SmsSuppressionModel.id)
                .where(
                    SmsSuppressionModel.tenant_id == tenant_id,
                    SmsSuppressionModel.phone_number == phone_number,
                )
                .limit(1)
            )
            return row is not None

    async def list_sms_suppressions(self, tenant_id: str, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        with session_scope() as session:
            rows = session.scalars(
                select(SmsSuppressionModel)
                .where(SmsSuppressionModel.tenant_id == tenant_id)
                .order_by(SmsSuppressionModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [_sms_suppression_to_dict(row) for row in rows]


postgres_store = PostgresStore()
