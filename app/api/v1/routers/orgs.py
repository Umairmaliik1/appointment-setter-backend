"""
Organization hierarchy API routes.
"""

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder

from app.api.v1.routers.auth import get_current_user_from_token
from app.api.v1.schemas.auth import UserCreate
from app.api.v1.schemas.org import (
    OrgCreateRequest,
    OrgLifecycleResponse,
    OrgMemberInviteRequest,
    OrgMemberInviteDispatchResponse,
    OrgMembershipCreateRequest,
    OrgMembershipResponse,
    OrgMemberResendInviteRequest,
    OrgResponse,
    OrgUpdateRequest,
    PartnerOwnerUserResponse,
    PartnerBrandingResponse,
    PartnerBrandingUpdateRequest,
    PartnerEntitlementsResponse,
    PartnerEntitlementsUpdateRequest,
    PartnerWithOwnerCreateRequest,
    PartnerWithOwnerResponse,
)
from app.api.v1.services.auth import auth_service
from app.core.config import PLATFORM_APP_BASE_URL
from app.core.platform_apps import normalize_allowed_app_ids
from app.models.auth import UserRole
from app.services.audit_service import audit_service
from app.services.email.service import email_service
from app.services.postgres_store import postgres_store
from app.services.org_service import PARTNER_ROLES, PLATFORM_ORG_ID, org_service

router = APIRouter(tags=["orgs"])

DEFAULT_PARTNER_SEAT_LIMIT = 25


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _role_name(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _as_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
        if parsed > 0:
            return parsed
    except (TypeError, ValueError):
        pass
    return default


def _request_hash(payload: Any) -> str:
    encoded = json.dumps(jsonable_encoder(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _build_setup_password_url(token: str, invite_base_url: Optional[str]) -> str:
    base = (invite_base_url or PLATFORM_APP_BASE_URL or "").strip().rstrip("/")
    if not base:
        base = "http://localhost:3000"
    return f"{base}/mindrind/admin/setup-password?token={token}"


def _is_platform_staff(current_user: Dict[str, Any]) -> bool:
    memberships = current_user.get("org_memberships") or []
    return str(current_user.get("role", "")).lower() == "admin" or org_service.is_platform_staff(memberships)


def _partner_scope_ids(current_user: Dict[str, Any]) -> Set[str]:
    memberships = current_user.get("org_memberships") or []
    partner_orgs: Set[str] = set()
    for membership in memberships:
        org_id = str(membership.get("org_id", "")).strip()
        if not org_id:
            continue
        if _role_name(membership.get("role")) in PARTNER_ROLES:
            partner_orgs.add(org_id)
    return partner_orgs


async def _collect_partner_org_scope_ids(partner_org_id: str) -> Set[str]:
    scope_ids: Set[str] = {partner_org_id}
    descendants = await postgres_store.list_descendant_orgs(partner_org_id)
    for org in descendants:
        org_id = str(org.get("id", "")).strip()
        if not org_id:
            continue
        org_type = str(org.get("org_type", "")).strip().lower()
        if org_type in {"customer", "partner"}:
            scope_ids.add(org_id)
    return scope_ids


async def _active_user_ids_for_partner_scope(partner_org_id: str) -> Set[str]:
    scope_org_ids = await _collect_partner_org_scope_ids(partner_org_id)
    active_user_ids: Set[str] = set()
    for scope_org_id in scope_org_ids:
        memberships = await postgres_store.list_org_memberships_for_org(scope_org_id)
        for membership in memberships:
            if _role_name(membership.get("status")) != "active":
                continue
            member_user_id = str(membership.get("user_id", "")).strip()
            if member_user_id:
                active_user_ids.add(member_user_id)
    return active_user_ids


async def _resolve_partner_org_id(org: Dict[str, Any]) -> Optional[str]:
    org_type = str(org.get("org_type", "")).strip().lower()
    if org_type == "partner":
        return str(org.get("id", "")).strip() or None
    if org_type == "customer":
        parent_org_id = str(org.get("parent_org_id", "")).strip()
        return parent_org_id or None
    return None


async def _sync_partner_seat_usage(partner_org_id: str) -> Dict[str, Any]:
    entitlements = await postgres_store.get_partner_entitlements(partner_org_id) or {}
    seat_limit = _as_positive_int(entitlements.get("seat_limit"), DEFAULT_PARTNER_SEAT_LIMIT)
    active_user_ids = await _active_user_ids_for_partner_scope(partner_org_id)
    seat_usage = len(active_user_ids)
    seat_remaining = max(seat_limit - seat_usage, 0)
    over_seat_limit = seat_usage > seat_limit
    update_payload = {
        "seat_limit": seat_limit,
        "seat_usage": seat_usage,
        "seat_remaining": seat_remaining,
        "over_seat_limit": over_seat_limit,
    }
    return await postgres_store.upsert_partner_entitlements(partner_org_id, update_payload)


async def _enforce_partner_seat_limit(partner_org_id: str, candidate_user_id: Optional[str] = None) -> None:
    entitlements = await postgres_store.get_partner_entitlements(partner_org_id) or {}
    seat_limit = _as_positive_int(entitlements.get("seat_limit"), DEFAULT_PARTNER_SEAT_LIMIT)
    active_user_ids = await _active_user_ids_for_partner_scope(partner_org_id)
    normalized_candidate = (candidate_user_id or "").strip()
    if normalized_candidate and normalized_candidate in active_user_ids:
        return
    projected_usage = len(active_user_ids) + 1
    if projected_usage > seat_limit:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Partner seat limit reached ({seat_limit}). Increase seat limit to add more users.",
        )


async def _cascade_customer_status_for_partner(partner_org_id: str, status_value: str) -> int:
    """
    Update all descendant customer organizations under a partner to a target status.

    Returns count of customer orgs updated.
    """
    descendants = await postgres_store.list_descendant_orgs(partner_org_id)
    updated_count = 0
    target_status = str(status_value).strip().lower()
    for org in descendants:
        if str(org.get("org_type", "")).strip().lower() != "customer":
            continue
        org_id = str(org.get("id", "")).strip()
        if not org_id:
            continue
        await postgres_store.update_org(org_id, {"status": target_status})
        updated_count += 1
    return updated_count


async def _require_org_access(current_user: Dict[str, Any], org_id: str) -> Dict[str, Any]:
    has_access = await org_service.user_can_access_org(current_user, org_id)
    if not has_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied for this organization")
    org = await postgres_store.get_org(org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return await _attach_partner_entitlements(org)


async def _attach_partner_entitlements(org: Dict[str, Any]) -> Dict[str, Any]:
    if str(org.get("org_type", "")).lower() != "partner":
        return org
    partner_org_id = str(org.get("id", ""))
    entitlements = await _sync_partner_seat_usage(partner_org_id)
    enriched = dict(org)
    enriched["appointment_setter_enabled"] = bool(entitlements.get("appointment_setter_enabled", False))
    seat_limit = _as_positive_int(entitlements.get("seat_limit"), DEFAULT_PARTNER_SEAT_LIMIT)
    seat_usage = _as_positive_int(entitlements.get("seat_usage"), 0)
    enriched["seat_limit"] = seat_limit
    enriched["seat_usage"] = seat_usage
    enriched["seat_remaining"] = max(seat_limit - seat_usage, 0)
    enriched["over_seat_limit"] = bool(entitlements.get("over_seat_limit", seat_usage > seat_limit))
    onboarding_status = str(entitlements.get("onboarding_status", "")).strip().lower()
    enriched["onboarding_status"] = onboarding_status if onboarding_status in {"invited", "active", "suspended"} else "invited"
    return enriched


async def _handle_idempotency_precheck(
    *,
    scope: str,
    key: Optional[str],
    payload: Any,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    normalized_key = (key or "").strip()
    if not normalized_key:
        return None

    state = await postgres_store.acquire_idempotency_key(
        scope=scope,
        key=normalized_key,
        request_hash=_request_hash(payload),
        user_id=user_id,
    )
    status_name = str(state.get("state", "")).lower()
    if status_name == "completed":
        record = state.get("record") or {}
        cached = record.get("response_payload")
        if cached is not None:
            return cached
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key already completed but response is unavailable",
        )
    if status_name == "conflict":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key was already used with a different request payload",
        )
    if status_name == "in_progress":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request with this idempotency key is already in progress",
        )
    if status_name == "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key belongs to a failed request, please retry with a new key",
        )
    return None


@router.get("/orgs", response_model=List[OrgResponse])
async def list_orgs(
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    org_type: Optional[str] = Query(None),
    parent_org_id: Optional[str] = Query(None),
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """List organizations visible to the current actor."""
    if _is_platform_staff(current_user):
        orgs = await postgres_store.list_orgs(limit=limit, offset=offset)
    else:
        orgs = current_user.get("accessible_orgs") or []

    filtered = orgs
    if org_type:
        filtered = [org for org in filtered if str(org.get("org_type", "")).lower() == org_type.lower()]
    if parent_org_id:
        filtered = [org for org in filtered if str(org.get("parent_org_id", "")) == parent_org_id]

    sliced = filtered[offset : offset + limit]
    return [await _attach_partner_entitlements(org) for org in sliced]


@router.post("/orgs", response_model=OrgResponse, status_code=status.HTTP_201_CREATED)
async def create_org(
    payload: OrgCreateRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """Create partner/customer org with policy checks."""
    platform_staff = _is_platform_staff(current_user)
    partner_scope_ids = _partner_scope_ids(current_user)

    if payload.org_type == "platform":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform organization is immutable and cannot be created via public API",
        )

    if not platform_staff:
        if payload.org_type != "customer":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only platform staff can create this org type")
        if not payload.parent_org_id or payload.parent_org_id not in partner_scope_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Partner staff can only create customers under their own partner org",
            )

    if payload.org_type == "partner":
        if not platform_staff:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only platform staff can create partner orgs")
        if not payload.parent_org_id:
            await org_service.ensure_platform_org_exists()
            parent_org_id = PLATFORM_ORG_ID
        else:
            if payload.parent_org_id != PLATFORM_ORG_ID:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="partner org must have parent_org_id set to mindrind-platform",
                )
            parent_org_id = payload.parent_org_id
    elif payload.org_type == "customer":
        if not payload.parent_org_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="customer org requires parent_org_id")
        parent_org_id = payload.parent_org_id
    else:
        parent_org_id = payload.parent_org_id

    if parent_org_id:
        parent_org = await postgres_store.get_org(parent_org_id)
        if not parent_org:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="parent_org_id does not exist")
        if payload.org_type == "customer" and str(parent_org.get("org_type", "")).lower() != "partner":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="customer org parent must be a partner org",
            )

    current_user_id = str(current_user.get("id", ""))
    normalized_idempotency_key = (idempotency_key or "").strip() or None
    cached_response = await _handle_idempotency_precheck(
        scope=f"orgs:create:{current_user_id}",
        key=normalized_idempotency_key,
        payload=payload.model_dump(mode="json", exclude_none=False),
        user_id=current_user_id,
    )
    if cached_response is not None:
        return cached_response

    org_id = str(uuid.uuid4())
    org_doc = {
        "id": org_id,
        "org_type": payload.org_type,
        "parent_org_id": parent_org_id,
        "name": payload.name.strip(),
        "status": payload.status,
        "branding": payload.branding.model_dump(exclude_none=True) if payload.branding else {},
        "legacy_tenant_id": payload.legacy_tenant_id,
    }
    try:
        created = await postgres_store.create_org(org_doc)

        if payload.org_type == "partner":
            await postgres_store.upsert_partner_entitlements(
                org_id,
                {
                    "appointment_setter_enabled": False,
                    "seat_limit": DEFAULT_PARTNER_SEAT_LIMIT,
                    "seat_usage": 0,
                    "seat_remaining": DEFAULT_PARTNER_SEAT_LIMIT,
                    "over_seat_limit": False,
                    "onboarding_status": "invited",
                    "approved_by_user_id": None,
                    "approval_notes": "Awaiting MindRind approval",
                },
            )

        response_payload = await _attach_partner_entitlements(created)
        await audit_service.log_event(
            actor=current_user,
            action="org.create",
            resource_type="org",
            resource_id=org_id,
            metadata={
                "org_type": payload.org_type,
                "name": payload.name.strip(),
                "parent_org_id": parent_org_id,
            },
        )
        if normalized_idempotency_key:
            await postgres_store.complete_idempotency_key(
                scope=f"orgs:create:{current_user_id}",
                key=normalized_idempotency_key,
                response_payload=jsonable_encoder(response_payload),
            )
        return response_payload
    except Exception as exc:
        if normalized_idempotency_key:
            await postgres_store.fail_idempotency_key(
                scope=f"orgs:create:{current_user_id}",
                key=normalized_idempotency_key,
                error_message=str(exc),
            )
        raise


@router.post("/orgs/partner-with-owner", response_model=PartnerWithOwnerResponse, status_code=status.HTTP_201_CREATED)
async def create_partner_with_owner(
    payload: PartnerWithOwnerCreateRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """Create partner org + owner user + membership in one API call."""
    if not _is_platform_staff(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform staff can create partner organizations",
        )

    await org_service.ensure_platform_org_exists()
    current_user_id = str(current_user.get("id", ""))
    idempotency_scope = f"orgs:partner-with-owner:{current_user_id}"
    normalized_idempotency_key = (idempotency_key or "").strip() or None
    cached_response = await _handle_idempotency_precheck(
        scope=idempotency_scope,
        key=normalized_idempotency_key,
        payload=payload.model_dump(mode="json", exclude_none=False),
        user_id=current_user_id,
    )
    if cached_response is not None:
        return cached_response

    allowed_app_ids = normalize_allowed_app_ids(payload.owner.allowed_app_ids) or ["appointment_setter", "users"]
    default_app_id = payload.owner.default_app_id if payload.owner.default_app_id in allowed_app_ids else allowed_app_ids[0]
    owner_email = str(payload.owner.email).strip().lower()
    partner_org_id: Optional[str] = None
    owner_user_id: Optional[str] = None

    async def _rollback_partial_creates() -> None:
        if partner_org_id and owner_user_id:
            await postgres_store.delete_org_membership(org_id=partner_org_id, user_id=owner_user_id)
            await postgres_store.delete_user(owner_user_id)
        if partner_org_id:
            await postgres_store.delete_partner_entitlements(partner_org_id)
            await postgres_store.delete_org(partner_org_id)

    try:
        existing_user = await auth_service.get_user_by_email(owner_email)
        if existing_user:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Owner email is already registered")

        partner_org_id = str(uuid.uuid4())
        org_doc = {
            "id": partner_org_id,
            "org_type": "partner",
            "parent_org_id": PLATFORM_ORG_ID,
            "name": payload.name.strip(),
            "status": payload.status,
            "branding": payload.branding.model_dump(exclude_none=True) if payload.branding else {},
            "legacy_tenant_id": payload.legacy_tenant_id,
        }
        created_partner = await postgres_store.create_org(org_doc)

        entitlements_record = await postgres_store.upsert_partner_entitlements(
            partner_org_id,
            {
                "appointment_setter_enabled": False,
                "seat_limit": payload.seat_limit,
                "seat_usage": 0,
                "seat_remaining": payload.seat_limit,
                "over_seat_limit": False,
                "onboarding_status": "invited",
                "approved_by_user_id": None,
                "approval_notes": payload.approval_notes or "Awaiting MindRind approval",
            },
        )

        owner_password = payload.owner.password or secrets.token_urlsafe(16)
        new_user = await auth_service.create_user(
            UserCreate(
                email=owner_email,
                username=payload.owner.username.strip(),
                password=owner_password,
                first_name=payload.owner.first_name.strip(),
                last_name=payload.owner.last_name.strip(),
                role=UserRole.TENANT_ADMIN,
            )
        )

        owner_user_id = str(new_user.get("id", ""))
        await org_service.create_membership(
            org_id=partner_org_id,
            user_id=owner_user_id,
            role=payload.owner.role,
            status="active",
        )
        membership_record = await postgres_store.get_org_membership(org_id=partner_org_id, user_id=owner_user_id)
        if not membership_record:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create owner membership for partner",
            )
        entitlements_record = await _sync_partner_seat_usage(partner_org_id)

        updated_user = await auth_service.update_user_fields(
            owner_user_id,
            {
                "allowed_app_ids": allowed_app_ids,
                "default_app_id": default_app_id,
                "active_org_id": partner_org_id,
            },
        )

        setup_token = auth_service.create_setup_password_token(owner_user_id, expires_hours=48)
        setup_password_url = _build_setup_password_url(setup_token, payload.invite_base_url)
        login_base_url = (payload.invite_base_url or PLATFORM_APP_BASE_URL or "").strip().rstrip("/") or "http://localhost:3000"
        login_url = f"{login_base_url}/login"
        invite_email_sent = False
        if payload.send_invite_email:
            invite_email_sent = await email_service.send_user_setup_invite(
                recipient_email=owner_email,
                recipient_name=f"{payload.owner.first_name.strip()} {payload.owner.last_name.strip()}".strip(),
                workspace_name=payload.name.strip(),
                setup_password_url=setup_password_url,
                login_url=login_url,
                expires_in_hours=48,
                platform_name="MindRind",
            )

        response_payload = {
            "partner_org": await _attach_partner_entitlements(created_partner),
            "owner_user": PartnerOwnerUserResponse(
                id=owner_user_id,
                email=str(updated_user.get("email", "")),
                username=str(updated_user.get("username", "")),
                first_name=str(updated_user.get("first_name", "")),
                last_name=str(updated_user.get("last_name", "")),
                role=str(updated_user.get("role", "")),
                active_org_id=updated_user.get("active_org_id"),
                allowed_app_ids=updated_user.get("allowed_app_ids", []) or [],
                default_app_id=updated_user.get("default_app_id"),
            ),
            "membership": membership_record,
            "entitlements": entitlements_record,
            "invite_email_sent": invite_email_sent,
        }
        await audit_service.log_event(
            actor=current_user,
            action="partner.onboard",
            resource_type="org",
            resource_id=partner_org_id,
            metadata={
                "partner_name": payload.name.strip(),
                "owner_user_id": owner_user_id,
                "owner_email": owner_email,
                "invite_email_sent": invite_email_sent,
            },
        )
        if normalized_idempotency_key:
            await postgres_store.complete_idempotency_key(
                scope=idempotency_scope,
                key=normalized_idempotency_key,
                response_payload=jsonable_encoder(response_payload),
            )
        return response_payload
    except HTTPException as exc:
        await _rollback_partial_creates()
        await audit_service.log_event(
            actor=current_user,
            action="partner.onboard",
            resource_type="org",
            resource_id=partner_org_id or "pending",
            status="failed",
            metadata={"error": str(exc.detail)},
        )
        if normalized_idempotency_key:
            await postgres_store.fail_idempotency_key(
                scope=idempotency_scope,
                key=normalized_idempotency_key,
                error_message=exc.detail if isinstance(exc.detail, str) else "Request failed",
            )
        raise
    except Exception as exc:
        await _rollback_partial_creates()
        await audit_service.log_event(
            actor=current_user,
            action="partner.onboard",
            resource_type="org",
            resource_id=partner_org_id or "pending",
            status="failed",
            metadata={"error": str(exc)},
        )
        if normalized_idempotency_key:
            await postgres_store.fail_idempotency_key(
                scope=idempotency_scope,
                key=normalized_idempotency_key,
                error_message=str(exc),
            )
        raise


@router.get("/orgs/{org_id}", response_model=OrgResponse)
async def get_org(org_id: str, current_user: Dict[str, Any] = Depends(get_current_user_from_token)):
    """Get org details by id."""
    return await _require_org_access(current_user, org_id)


@router.put("/orgs/{org_id}", response_model=OrgResponse)
async def update_org(
    org_id: str,
    payload: OrgUpdateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """Update organization details."""
    org = await _require_org_access(current_user, org_id)
    if org_id == PLATFORM_ORG_ID or str(org.get("org_type", "")).lower() == "platform":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform organization is immutable and cannot be updated via public API",
        )

    update_data: Dict[str, Any] = {}
    if payload.name is not None:
        update_data["name"] = payload.name.strip()
    if payload.status is not None:
        update_data["status"] = payload.status
    if payload.branding is not None:
        update_data["branding"] = payload.branding.model_dump(exclude_none=True)

    updated = await postgres_store.update_org(org_id, update_data)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    if str(updated.get("org_type", "")).lower() == "partner":
        if payload.status == "suspended":
            await postgres_store.upsert_partner_entitlements(org_id, {"onboarding_status": "suspended"})
        elif payload.status == "active":
            await postgres_store.upsert_partner_entitlements(org_id, {"onboarding_status": "active"})
        await _sync_partner_seat_usage(org_id)
    await audit_service.log_event(
        actor=current_user,
        action="org.update",
        resource_type="org",
        resource_id=org_id,
        metadata={"changed_fields": sorted(list(update_data.keys()))},
    )
    return await _attach_partner_entitlements(updated)


@router.post("/orgs/{org_id}/suspend", response_model=OrgLifecycleResponse)
async def suspend_org(
    org_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """Suspend partner/customer org from operating."""
    org = await _require_org_access(current_user, org_id)
    org_type = str(org.get("org_type", "")).lower()
    if org_id == PLATFORM_ORG_ID or org_type == "platform":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform organization is immutable and cannot be suspended",
        )
    if org_type == "partner" and not _is_platform_staff(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform staff can suspend partner organizations",
        )

    updated = await postgres_store.update_org(org_id, {"status": "suspended"})
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    onboarding_status: Optional[str] = None
    affected_customers = 0
    if org_type == "partner":
        affected_customers = await _cascade_customer_status_for_partner(org_id, "suspended")
        entitlements = await postgres_store.upsert_partner_entitlements(org_id, {"onboarding_status": "suspended"})
        entitlements = await _sync_partner_seat_usage(org_id)
        onboarding_status = str(entitlements.get("onboarding_status", "suspended"))

    await audit_service.log_event(
        actor=current_user,
        action="org.suspend",
        resource_type="org",
        resource_id=org_id,
        metadata={"org_type": org_type, "affected_customers": affected_customers},
    )
    return OrgLifecycleResponse(
        org_id=org_id,
        status="suspended",
        onboarding_status=onboarding_status,
        updated_at=_utcnow(),
    )


@router.post("/orgs/{org_id}/reactivate", response_model=OrgLifecycleResponse)
async def reactivate_org(
    org_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """Reactivate partner/customer org."""
    org = await _require_org_access(current_user, org_id)
    org_type = str(org.get("org_type", "")).lower()
    if org_id == PLATFORM_ORG_ID or org_type == "platform":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform organization is immutable and cannot be reactivated",
        )
    if org_type == "partner" and not _is_platform_staff(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform staff can reactivate partner organizations",
        )

    updated = await postgres_store.update_org(org_id, {"status": "active"})
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    onboarding_status: Optional[str] = None
    affected_customers = 0
    if org_type == "partner":
        affected_customers = await _cascade_customer_status_for_partner(org_id, "active")
        entitlements = await postgres_store.upsert_partner_entitlements(org_id, {"onboarding_status": "active"})
        entitlements = await _sync_partner_seat_usage(org_id)
        onboarding_status = str(entitlements.get("onboarding_status", "active"))

    await audit_service.log_event(
        actor=current_user,
        action="org.reactivate",
        resource_type="org",
        resource_id=org_id,
        metadata={"org_type": org_type, "affected_customers": affected_customers},
    )
    return OrgLifecycleResponse(
        org_id=org_id,
        status="active",
        onboarding_status=onboarding_status,
        updated_at=_utcnow(),
    )


@router.get("/orgs/{org_id}/members", response_model=List[OrgMembershipResponse])
async def list_org_members(
    org_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """List members for an org."""
    await _require_org_access(current_user, org_id)
    return await postgres_store.list_org_memberships_for_org(org_id)


@router.post("/orgs/{org_id}/members", response_model=OrgMembershipResponse, status_code=status.HTTP_201_CREATED)
async def create_org_member(
    org_id: str,
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """Create org membership directly, or invite/create user then attach membership."""
    org = await _require_org_access(current_user, org_id)
    platform_staff = _is_platform_staff(current_user)
    if not platform_staff and str(org.get("org_type")) == "platform":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only platform staff can manage platform org members")
    if str(org.get("status", "active")).strip().lower() in {"inactive", "suspended"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot add members to an inactive or suspended organization",
        )

    partner_org_id = await _resolve_partner_org_id(org)

    if "user_id" in payload:
        membership_payload = OrgMembershipCreateRequest(**payload)
        existing_membership = await postgres_store.get_org_membership(org_id=org_id, user_id=membership_payload.user_id)
        if partner_org_id and _role_name(membership_payload.status) == "active":
            existing_is_active = _role_name((existing_membership or {}).get("status")) == "active"
            if not existing_is_active:
                await _enforce_partner_seat_limit(partner_org_id, candidate_user_id=membership_payload.user_id)
        created = await org_service.create_membership(
            org_id=org_id,
            user_id=membership_payload.user_id,
            role=membership_payload.role,
            status=membership_payload.status,
        )
        if partner_org_id:
            await _sync_partner_seat_usage(partner_org_id)
        created_record = await postgres_store.get_org_membership(org_id=org_id, user_id=membership_payload.user_id)
        await audit_service.log_event(
            actor=current_user,
            action="org.membership.create",
            resource_type="org_membership",
            resource_id=created.get("id", f"{org_id}:{membership_payload.user_id}"),
            metadata={
                "org_id": org_id,
                "user_id": membership_payload.user_id,
                "role": membership_payload.role,
                "status": membership_payload.status,
            },
        )
        return created_record or created

    invite_payload = OrgMemberInviteRequest(**payload)
    if partner_org_id and _role_name(invite_payload.status) == "active":
        await _enforce_partner_seat_limit(partner_org_id)
    password = invite_payload.password or secrets.token_urlsafe(16)
    org_type = str(org.get("org_type", "")).lower()
    if org_type == "platform":
        base_role = UserRole.ADMIN
    elif _role_name(invite_payload.role) in {"partner_owner", "partner_admin"}:
        base_role = UserRole.TENANT_ADMIN
    else:
        base_role = UserRole.TENANT_USER

    new_user = await auth_service.create_user(
        UserCreate(
            email=invite_payload.email,
            username=invite_payload.username,
            password=password,
            first_name=invite_payload.first_name,
            last_name=invite_payload.last_name,
            role=base_role,
        )
    )
    await org_service.create_membership(
        org_id=org_id,
        user_id=str(new_user["id"]),
        role=invite_payload.role,
        status=invite_payload.status,
    )
    if partner_org_id:
        await _sync_partner_seat_usage(partner_org_id)
    invite_email_sent = False
    if invite_payload.send_invite_email:
        invited_user_id = str(new_user["id"])
        setup_token = auth_service.create_setup_password_token(invited_user_id, expires_hours=48)
        setup_password_url = _build_setup_password_url(setup_token, invite_payload.invite_base_url)
        login_base_url = (invite_payload.invite_base_url or PLATFORM_APP_BASE_URL or "").strip().rstrip("/") or "http://localhost:3000"
        login_url = f"{login_base_url}/login"
        invite_email_sent = await email_service.send_user_setup_invite(
            recipient_email=str(invite_payload.email).strip().lower(),
            recipient_name=f"{invite_payload.first_name.strip()} {invite_payload.last_name.strip()}".strip(),
            workspace_name=str(org.get("name", "Workspace")),
            setup_password_url=setup_password_url,
            login_url=login_url,
            expires_in_hours=48,
            platform_name="MindRind",
        )
    created_record = await postgres_store.get_org_membership(org_id=org_id, user_id=str(new_user["id"]))
    if created_record:
        await audit_service.log_event(
            actor=current_user,
            action="org.member.invite",
            resource_type="org_membership",
            resource_id=created_record.get("id", f"{org_id}:{new_user['id']}"),
            metadata={
                "org_id": org_id,
                "invited_user_id": str(new_user["id"]),
                "invited_email": invite_payload.email,
                "role": invite_payload.role,
                "invite_email_sent": invite_email_sent,
            },
        )
        return created_record
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create org membership")


@router.post("/orgs/{org_id}/members/{user_id}/resend-setup-invite", response_model=OrgMemberInviteDispatchResponse)
async def resend_org_member_setup_invite(
    org_id: str,
    user_id: str,
    payload: OrgMemberResendInviteRequest,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """Resend setup-password invite for an existing org member."""
    org = await _require_org_access(current_user, org_id)
    platform_staff = _is_platform_staff(current_user)
    if not platform_staff and str(org.get("org_type", "")).lower() == "platform":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only platform staff can manage platform org members")

    membership = await postgres_store.get_org_membership(org_id=org_id, user_id=user_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org membership not found")

    user_doc = await auth_service.get_user(user_id)
    if not user_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    setup_token = auth_service.create_setup_password_token(user_id, expires_hours=48)
    setup_password_url = _build_setup_password_url(setup_token, payload.invite_base_url)
    login_base_url = (payload.invite_base_url or PLATFORM_APP_BASE_URL or "").strip().rstrip("/") or "http://localhost:3000"
    login_url = f"{login_base_url}/login"
    recipient_email = str(user_doc.get("email", "")).strip().lower()
    recipient_name = f"{str(user_doc.get('first_name', '')).strip()} {str(user_doc.get('last_name', '')).strip()}".strip() or "there"
    invite_email_sent = await email_service.send_user_setup_invite(
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        workspace_name=str(org.get("name", "Workspace")),
        setup_password_url=setup_password_url,
        login_url=login_url,
        expires_in_hours=48,
        platform_name="MindRind",
    )
    await audit_service.log_event(
        actor=current_user,
        action="org.member.invite.resend",
        resource_type="org_membership",
        resource_id=str(membership.get("id", f"{org_id}:{user_id}")),
        metadata={
            "org_id": org_id,
            "user_id": user_id,
            "recipient_email": recipient_email,
            "invite_email_sent": invite_email_sent,
        },
    )

    return OrgMemberInviteDispatchResponse(
        org_id=org_id,
        user_id=user_id,
        invite_email_sent=invite_email_sent,
        message="Invite email sent" if invite_email_sent else "Invite email dispatch failed",
    )


@router.put("/partners/{partner_org_id}/entitlements", response_model=PartnerEntitlementsResponse)
async def update_partner_entitlements(
    partner_org_id: str,
    payload: PartnerEntitlementsUpdateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """MindRind approval gate for partner app entitlements."""
    if not _is_platform_staff(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only platform staff can update partner entitlements")

    org = await postgres_store.get_org(partner_org_id)
    if not org or str(org.get("org_type", "")).lower() != "partner":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Partner organization not found")

    update_payload: Dict[str, Any] = {
        "appointment_setter_enabled": payload.appointment_setter_enabled,
        "approved_by_user_id": payload.approved_by_user_id or str(current_user.get("id", "")),
        "approval_notes": payload.approval_notes,
        "updated_at": _utcnow().isoformat(),
    }
    if payload.seat_limit is not None:
        update_payload["seat_limit"] = payload.seat_limit
    if payload.onboarding_status is not None:
        update_payload["onboarding_status"] = payload.onboarding_status

    await postgres_store.upsert_partner_entitlements(
        partner_org_id=partner_org_id,
        payload=update_payload,
    )
    record = await _sync_partner_seat_usage(partner_org_id)
    await audit_service.log_event(
        actor=current_user,
        action="partner.entitlements.update",
        resource_type="partner_entitlements",
        resource_id=partner_org_id,
        metadata={
            "appointment_setter_enabled": payload.appointment_setter_enabled,
            "approval_notes": payload.approval_notes,
            "seat_limit": payload.seat_limit,
            "onboarding_status": payload.onboarding_status,
        },
    )
    return record


@router.put("/partners/{partner_org_id}/branding", response_model=PartnerBrandingResponse)
async def update_partner_branding(
    partner_org_id: str,
    payload: PartnerBrandingUpdateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """Update partner branding values."""
    org = await _require_org_access(current_user, partner_org_id)
    if str(org.get("org_type", "")).lower() != "partner":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Branding endpoint only supports partner orgs")

    if not _is_platform_staff(current_user):
        allowed_partner_ids = _partner_scope_ids(current_user)
        if partner_org_id not in allowed_partner_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owner partner staff can update this branding")

    updated = await postgres_store.update_org(partner_org_id, {"branding": payload.branding.model_dump(exclude_none=True)})
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Partner organization not found")
    await audit_service.log_event(
        actor=current_user,
        action="partner.branding.update",
        resource_type="org",
        resource_id=partner_org_id,
        metadata={"branding_keys": sorted(list(payload.branding.model_dump(exclude_none=True).keys()))},
    )

    return {
        "partner_org_id": partner_org_id,
        "branding": updated.get("branding", {}),
        "updated_at": _utcnow(),
    }
