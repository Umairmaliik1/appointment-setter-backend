"""
Platform bootstrap and app-access API routes.
"""

from datetime import datetime, timezone
import hashlib
import json
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.api.v1.routers.auth import get_current_user_from_token, require_admin_role
from app.api.v1.schemas.auth import UserCreate
from app.api.v1.services.auth import auth_service
from app.core.platform_apps import (
    get_platform_apps,
    normalize_allowed_app_ids,
    resolve_user_allowed_app_ids,
    resolve_user_default_app_id,
)
from app.models.auth import UserRole
from app.services.audit_service import audit_service
from app.services.org_service import PLATFORM_ORG_ID, org_service
from app.services.postgres_store import postgres_store

router = APIRouter(prefix="/platform", tags=["platform"])


class PlatformAppResponse(BaseModel):
    id: str
    slug: str
    label: str
    default_route: str
    icon_key: str
    description: str


class PlatformActorResponse(BaseModel):
    id: str
    email: str
    first_name: str
    last_name: str
    role: str
    platform_role_id: Optional[str] = None
    platform_role_name: Optional[str] = None
    platform_role_slug: Optional[str] = None
    memberships: List[Dict[str, Any]] = Field(default_factory=list)


class PlatformOrgSummaryResponse(BaseModel):
    id: str
    org_type: str
    parent_org_id: Optional[str] = None
    legacy_tenant_id: Optional[str] = None
    name: str
    status: str


class PlatformEntitlementsResponse(BaseModel):
    appointment_setter_enabled: bool = True
    approved_by_user_id: Optional[str] = None
    approval_notes: Optional[str] = None
    updated_at: Optional[datetime] = None


class PlatformBootstrapResponse(BaseModel):
    actor: PlatformActorResponse
    active_org: Optional[PlatformOrgSummaryResponse] = None
    accessible_orgs: List[PlatformOrgSummaryResponse] = Field(default_factory=list)
    branding: Dict[str, Any] = Field(default_factory=dict)
    entitlements: PlatformEntitlementsResponse = Field(default_factory=PlatformEntitlementsResponse)
    apps: List[PlatformAppResponse] = Field(default_factory=list)
    allowed_app_ids: List[str] = Field(default_factory=list)
    default_app_id: str
    allowed_app_ids_effective: List[str] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)

    # Backward compatibility for already integrated clients.
    user: Dict[str, Any] = Field(default_factory=dict)


class UserAppAccessUpdateRequest(BaseModel):
    allowed_app_ids: List[str] = Field(default_factory=list)
    default_app_id: Optional[str] = None


class UserAppAccessUpdateResponse(BaseModel):
    user_id: str
    allowed_app_ids: List[str]
    default_app_id: str


class ActiveOrgUpdateRequest(BaseModel):
    org_id: str


class ActiveOrgUpdateResponse(BaseModel):
    user_id: str
    active_org_id: str


class PermissionCatalogItem(BaseModel):
    id: str
    label: str
    description: str


class PlatformRoleCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    description: Optional[str] = Field(None, max_length=240)
    scope: str = Field(default="platform", pattern="^platform$")
    permissions: List[str] = Field(default_factory=list)


class PlatformRoleUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=80)
    description: Optional[str] = Field(None, max_length=240)
    permissions: Optional[List[str]] = None
    status: Optional[str] = Field(None, pattern="^(active|inactive)$")


class PlatformRoleResponse(BaseModel):
    id: str
    name: str
    slug: str
    scope: str = "platform"
    description: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)
    status: str
    is_system: bool = False
    created_by_user_id: str


class PlatformRoleTemplateResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    scope: str = "platform"
    permissions: List[str] = Field(default_factory=list)


class PlatformUserCreateRequest(BaseModel):
    email: str
    username: str = Field(..., min_length=3, max_length=72)
    password: str = Field(..., min_length=8, max_length=72)
    first_name: str = Field(..., min_length=1, max_length=72)
    last_name: str = Field(..., min_length=1, max_length=72)
    platform_role_id: Optional[str] = None
    allowed_app_ids: List[str] = Field(default_factory=lambda: ["users"])
    default_app_id: Optional[str] = "users"
    membership_role: str = Field(default="platform_staff", pattern="^(platform_owner|platform_staff)$")


class PlatformUserRoleAssignRequest(BaseModel):
    platform_role_id: str


class PlatformUserResponse(BaseModel):
    id: str
    email: str
    username: str
    first_name: str
    last_name: str
    role: str
    platform_role_id: Optional[str] = None
    allowed_app_ids: List[str] = Field(default_factory=list)
    default_app_id: Optional[str] = None


class AuditLogActorResponse(BaseModel):
    user_id: str
    email: str
    role: str
    active_org_id: Optional[str] = None


class AuditLogResponse(BaseModel):
    id: str
    action: str
    resource_type: str
    resource_id: str
    status: str
    actor: AuditLogActorResponse
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


def _platform_membership_roles(current_user: Dict[str, Any]) -> List[str]:
    memberships = current_user.get("org_memberships") or []
    return [str(membership.get("role", "")).strip().lower() for membership in memberships]


def _is_platform_owner_actor(current_user: Dict[str, Any]) -> bool:
    actor_role = str(current_user.get("role", "")).strip().lower()
    if actor_role == "admin":
        return True
    return "platform_owner" in set(_platform_membership_roles(current_user))


async def _require_platform_permission(current_user: Dict[str, Any], permissions: List[str]) -> None:
    """
    Enforce platform permission catalog for non-owner platform users.

    Platform owners/global admins bypass this check. Platform staff must have
    at least one of the requested permissions (or admin:all via legacy roles).
    """
    if _is_platform_owner_actor(current_user):
        return

    memberships = current_user.get("org_memberships") or []
    if not org_service.is_platform_staff(memberships):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform access required")

    granted = set(await auth_service.get_user_permissions(str(current_user.get("id", ""))))
    if "admin:all" in granted:
        return
    if not any(permission in granted for permission in permissions):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient platform permissions")


def _parse_optional_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _slugify_role_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "role"


def _request_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(jsonable_encoder(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


PLATFORM_PERMISSION_CATALOG: List[Dict[str, str]] = [
    {"id": "users:read", "label": "Users Read", "description": "View platform users"},
    {"id": "users:write", "label": "Users Write", "description": "Create/update platform users"},
    {"id": "roles:read", "label": "Roles Read", "description": "View custom platform roles"},
    {"id": "roles:write", "label": "Roles Write", "description": "Create/update custom platform roles"},
    {"id": "partners:read", "label": "Partners Read", "description": "View partner organizations"},
    {"id": "partners:write", "label": "Partners Write", "description": "Manage partner organizations"},
    {"id": "customers:read", "label": "Customers Read", "description": "View customer organizations"},
    {"id": "customers:write", "label": "Customers Write", "description": "Manage customer organizations"},
    {"id": "entitlements:write", "label": "Entitlements Write", "description": "Toggle app entitlements"},
    {"id": "appointment_setter:read", "label": "Appointment Setter Read", "description": "View appointment setter data"},
    {"id": "appointment_setter:write", "label": "Appointment Setter Write", "description": "Manage appointment setter data"},
]

PLATFORM_ROLE_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "platform_viewer",
        "name": "Viewer",
        "description": "Read-only platform access for users, roles, partners, and customers.",
        "scope": "platform",
        "permissions": ["users:read", "roles:read", "partners:read", "customers:read"],
    },
    {
        "id": "platform_operator",
        "name": "Operator",
        "description": "Operational access for onboarding partners/customers and viewing users/roles.",
        "scope": "platform",
        "permissions": ["users:read", "roles:read", "partners:read", "partners:write", "customers:read", "customers:write"],
    },
    {
        "id": "platform_manager",
        "name": "Manager",
        "description": "Management access including user administration and entitlement toggles.",
        "scope": "platform",
        "permissions": [
            "users:read",
            "users:write",
            "roles:read",
            "partners:read",
            "partners:write",
            "customers:read",
            "customers:write",
            "entitlements:write",
        ],
    },
    {
        "id": "platform_owner",
        "name": "Owner",
        "description": "Full platform role-based access (except global admin bypass).",
        "scope": "platform",
        "permissions": [item["id"] for item in PLATFORM_PERMISSION_CATALOG],
    },
]


def _org_summary(org: Dict[str, Any]) -> PlatformOrgSummaryResponse:
    return PlatformOrgSummaryResponse(
        id=str(org.get("id", "")),
        org_type=str(org.get("org_type", "")),
        parent_org_id=org.get("parent_org_id"),
        legacy_tenant_id=org.get("legacy_tenant_id"),
        name=str(org.get("name", "")),
        status=str(org.get("status", "active")),
    )


def _compute_branding(active_org: Optional[Dict[str, Any]], partner_org: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    fallback = {
        "brand_name": "MindRind",
        "logo_url": None,
        "primary_color": "#0f172a",
        "secondary_color": "#ffffff",
        "accent_color": "#f59e0b",
    }
    if partner_org and isinstance(partner_org.get("branding"), dict):
        fallback.update({k: v for k, v in partner_org.get("branding", {}).items() if v is not None})
    if active_org and isinstance(active_org.get("branding"), dict):
        fallback.update({k: v for k, v in active_org.get("branding", {}).items() if v is not None})
    return fallback


def _to_platform_user_response(user: Dict[str, Any]) -> PlatformUserResponse:
    return PlatformUserResponse(
        id=str(user.get("id", "")),
        email=user.get("email", ""),
        username=user.get("username", ""),
        first_name=user.get("first_name", ""),
        last_name=user.get("last_name", ""),
        role=str(user.get("role", "user")),
        platform_role_id=user.get("platform_role_id"),
        allowed_app_ids=user.get("allowed_app_ids", []) or [],
        default_app_id=user.get("default_app_id"),
    )


@router.get("/bootstrap", response_model=PlatformBootstrapResponse)
async def get_platform_bootstrap(current_user: Dict[str, Any] = Depends(get_current_user_from_token)):
    access_context = await org_service.get_access_context(current_user)
    allowed_app_ids = resolve_user_allowed_app_ids(current_user)
    explicit_app_access = normalize_allowed_app_ids(current_user.get("allowed_app_ids"))
    if access_context.platform_scope and not explicit_app_access:
        allowed_app_ids = [app["id"] for app in get_platform_apps()]
    default_app_id = resolve_user_default_app_id(current_user, allowed_app_ids)
    permissions = await auth_service.get_user_permissions(str(current_user.get("id", "")))

    partner_org = await org_service.get_partner_org_for_active_context(access_context.active_org)
    partner_entitlements = None
    if partner_org:
        partner_entitlements = await postgres_store.get_partner_entitlements(str(partner_org.get("id", "")))

    is_platform_scope = access_context.platform_scope
    appointment_enabled = True
    if not is_platform_scope and partner_entitlements is not None:
        appointment_enabled = bool(partner_entitlements.get("appointment_setter_enabled", False))
    elif not is_platform_scope and partner_org:
        appointment_enabled = False

    effective_allowed = list(allowed_app_ids)
    if not appointment_enabled:
        effective_allowed = [app_id for app_id in effective_allowed if app_id != "appointment_setter"]

    if not effective_allowed:
        effective_allowed = ["users"]

    app_catalog = [PlatformAppResponse(**app) for app in get_platform_apps() if app["id"] in effective_allowed]
    if not app_catalog:
        app_catalog = [PlatformAppResponse(**app) for app in get_platform_apps() if app["id"] == "users"]
        if app_catalog:
            effective_allowed = [app_catalog[0].id]

    effective_default = default_app_id
    if effective_default not in effective_allowed and effective_allowed:
        effective_default = effective_allowed[0]

    platform_role_id = current_user.get("platform_role_id")
    platform_role_name: Optional[str] = None
    platform_role_slug: Optional[str] = None
    if isinstance(platform_role_id, str) and platform_role_id:
        role_doc = await postgres_store.get_platform_role(platform_role_id)
        if role_doc:
            role_name_raw = role_doc.get("name")
            role_slug_raw = role_doc.get("slug")
            platform_role_name = (str(role_name_raw).strip() or None) if role_name_raw is not None else None
            platform_role_slug = (str(role_slug_raw).strip() or None) if role_slug_raw is not None else None

    actor_payload = PlatformActorResponse(
        id=str(current_user.get("id", "")),
        email=current_user.get("email", ""),
        first_name=current_user.get("first_name", ""),
        last_name=current_user.get("last_name", ""),
        role=str(current_user.get("role", "user")),
        platform_role_id=platform_role_id,
        platform_role_name=platform_role_name,
        platform_role_slug=platform_role_slug,
        memberships=access_context.memberships,
    )
    active_org_summary = _org_summary(access_context.active_org) if access_context.active_org else None
    accessible_summaries = [_org_summary(org) for org in access_context.accessible_orgs]
    branding = _compute_branding(access_context.active_org, partner_org)
    entitlements_payload = PlatformEntitlementsResponse(
        appointment_setter_enabled=appointment_enabled,
        approved_by_user_id=partner_entitlements.get("approved_by_user_id") if partner_entitlements else None,
        approval_notes=partner_entitlements.get("approval_notes") if partner_entitlements else None,
        updated_at=_parse_optional_dt(partner_entitlements.get("updated_at")) if partner_entitlements else None,
    )

    return PlatformBootstrapResponse(
        actor=actor_payload,
        active_org=active_org_summary,
        accessible_orgs=accessible_summaries,
        branding=branding,
        entitlements=entitlements_payload,
        apps=app_catalog,
        allowed_app_ids=allowed_app_ids,
        default_app_id=default_app_id,
        allowed_app_ids_effective=effective_allowed,
        permissions=permissions,
        user={
            "id": actor_payload.id,
            "email": actor_payload.email,
            "first_name": actor_payload.first_name,
            "last_name": actor_payload.last_name,
            "role": actor_payload.role,
            "platform_role_id": actor_payload.platform_role_id,
            "platform_role_name": actor_payload.platform_role_name,
            "platform_role_slug": actor_payload.platform_role_slug,
            "allowed_app_ids": effective_allowed,
            "default_app_id": effective_default if effective_allowed else default_app_id,
        },
    )


@router.put("/users/{user_id}/app-access", response_model=UserAppAccessUpdateResponse)
async def update_user_app_access(
    user_id: str,
    payload: UserAppAccessUpdateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    require_admin_role(current_user)
    await _require_platform_permission(current_user, ["users:write"])

    target_user = await auth_service.get_user(user_id)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    allowed_app_ids = normalize_allowed_app_ids(payload.allowed_app_ids)
    if not allowed_app_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="allowed_app_ids must contain at least one valid app id",
        )

    default_app_id = payload.default_app_id or target_user.get("default_app_id")
    if not default_app_id or default_app_id not in allowed_app_ids:
        default_app_id = allowed_app_ids[0]

    await auth_service.update_user_fields(
        user_id,
        {
            "allowed_app_ids": allowed_app_ids,
            "default_app_id": default_app_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    await audit_service.log_event(
        actor=current_user,
        action="platform.user.app_access.update",
        resource_type="user",
        resource_id=user_id,
        metadata={
            "allowed_app_ids": allowed_app_ids,
            "default_app_id": default_app_id,
        },
    )

    return UserAppAccessUpdateResponse(
        user_id=user_id,
        allowed_app_ids=allowed_app_ids,
        default_app_id=default_app_id,
    )


@router.get("/permissions-catalog", response_model=List[PermissionCatalogItem])
async def list_platform_permission_catalog(current_user: Dict[str, Any] = Depends(get_current_user_from_token)):
    require_admin_role(current_user)
    await _require_platform_permission(current_user, ["roles:read", "roles:write"])
    return [PermissionCatalogItem(**item) for item in PLATFORM_PERMISSION_CATALOG]


@router.get("/audit-logs", response_model=List[AuditLogResponse])
async def list_platform_audit_logs(
    limit: int = 100,
    offset: int = 0,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """List platform audit logs (admin/staff only)."""
    require_admin_role(current_user)
    logs = await postgres_store.list_audit_logs(limit=limit, offset=offset)
    response: List[AuditLogResponse] = []
    for item in logs:
        actor = item.get("actor", {}) or {}
        response.append(
            AuditLogResponse(
                id=str(item.get("id", "")),
                action=str(item.get("action", "")),
                resource_type=str(item.get("resource_type", "")),
                resource_id=str(item.get("resource_id", "")),
                status=str(item.get("status", "success")),
                actor=AuditLogActorResponse(
                    user_id=str(actor.get("user_id", "")),
                    email=str(actor.get("email", "")),
                    role=str(actor.get("role", "")),
                    active_org_id=actor.get("active_org_id"),
                ),
                metadata=item.get("metadata", {}) or {},
                created_at=_parse_optional_dt(item.get("created_at")) or datetime.now(timezone.utc),
            )
        )
    return response


@router.get("/roles", response_model=List[PlatformRoleResponse])
async def list_platform_roles(current_user: Dict[str, Any] = Depends(get_current_user_from_token)):
    require_admin_role(current_user)
    await _require_platform_permission(current_user, ["roles:read", "roles:write"])
    roles = await postgres_store.list_platform_roles(limit=500, offset=0)
    normalized_roles = sorted(roles, key=lambda role: str(role.get("name", "")).lower())
    return [
        PlatformRoleResponse(
            id=str(role.get("id", "")),
            name=role.get("name", ""),
            slug=role.get("slug", ""),
            scope=str(role.get("scope", "platform")),
            description=role.get("description"),
            permissions=role.get("permissions", []) or [],
            status=role.get("status", "active"),
            is_system=bool(role.get("is_system", False)),
            created_by_user_id=role.get("created_by_user_id", ""),
        )
        for role in normalized_roles
    ]


@router.get("/role-templates", response_model=List[PlatformRoleTemplateResponse])
async def list_platform_role_templates(current_user: Dict[str, Any] = Depends(get_current_user_from_token)):
    require_admin_role(current_user)
    await _require_platform_permission(current_user, ["roles:read", "roles:write"])
    valid_permission_ids = {item["id"] for item in PLATFORM_PERMISSION_CATALOG}
    templates: List[PlatformRoleTemplateResponse] = []
    for template in PLATFORM_ROLE_TEMPLATES:
        template_permissions = [
            str(permission).strip()
            for permission in (template.get("permissions") or [])
            if str(permission).strip() in valid_permission_ids
        ]
        templates.append(
            PlatformRoleTemplateResponse(
                id=str(template.get("id", "")),
                name=str(template.get("name", "")),
                description=template.get("description"),
                scope=str(template.get("scope", "platform")),
                permissions=template_permissions,
            )
        )
    return templates


@router.post("/roles", response_model=PlatformRoleResponse, status_code=status.HTTP_201_CREATED)
async def create_platform_role(
    payload: PlatformRoleCreateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    require_admin_role(current_user)
    await _require_platform_permission(current_user, ["roles:write"])
    if payload.scope != "platform":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported role scope")
    slug = _slugify_role_name(payload.name)
    existing = await postgres_store.get_platform_role_by_slug(slug)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Role with this name already exists")

    catalog_ids = {item["id"] for item in PLATFORM_PERMISSION_CATALOG}
    permissions = [permission for permission in payload.permissions if permission in catalog_ids]

    role_doc = await postgres_store.create_platform_role(
        {
            "id": str(uuid.uuid4()),
            "name": payload.name.strip(),
            "slug": slug,
            "description": payload.description,
            "scope": payload.scope,
            "permissions": permissions,
            "status": "active",
            "is_system": False,
            "created_by_user_id": str(current_user.get("id", "")),
        }
    )
    await audit_service.log_event(
        actor=current_user,
        action="platform.role.create",
        resource_type="platform_role",
        resource_id=str(role_doc.get("id", "")),
        metadata={"name": role_doc.get("name", ""), "permissions": permissions},
    )

    return PlatformRoleResponse(
        id=str(role_doc.get("id", "")),
        name=role_doc.get("name", ""),
        slug=role_doc.get("slug", ""),
        scope=str(role_doc.get("scope", "platform")),
        description=role_doc.get("description"),
        permissions=role_doc.get("permissions", []) or [],
        status=role_doc.get("status", "active"),
        is_system=bool(role_doc.get("is_system", False)),
        created_by_user_id=role_doc.get("created_by_user_id", ""),
    )


@router.put("/roles/{role_id}", response_model=PlatformRoleResponse)
async def update_platform_role(
    role_id: str,
    payload: PlatformRoleUpdateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    require_admin_role(current_user)
    await _require_platform_permission(current_user, ["roles:write"])
    existing = await postgres_store.get_platform_role(role_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    if existing.get("is_system"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="System role cannot be modified")

    update_data: Dict[str, Any] = {}
    if payload.name is not None:
        new_slug = _slugify_role_name(payload.name)
        if new_slug != existing.get("slug"):
            slug_collision = await postgres_store.get_platform_role_by_slug(new_slug)
            if slug_collision and slug_collision.get("id") != role_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Role with this name already exists")
        update_data["name"] = payload.name.strip()
        update_data["slug"] = new_slug
    if payload.description is not None:
        update_data["description"] = payload.description
    if payload.permissions is not None:
        catalog_ids = {item["id"] for item in PLATFORM_PERMISSION_CATALOG}
        update_data["permissions"] = [permission for permission in payload.permissions if permission in catalog_ids]
    if payload.status is not None:
        update_data["status"] = payload.status

    updated = await postgres_store.update_platform_role(role_id, update_data)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    await audit_service.log_event(
        actor=current_user,
        action="platform.role.update",
        resource_type="platform_role",
        resource_id=role_id,
        metadata={"changed_fields": sorted(list(update_data.keys()))},
    )

    return PlatformRoleResponse(
        id=str(updated.get("id", "")),
        name=updated.get("name", ""),
        slug=updated.get("slug", ""),
        scope=str(updated.get("scope", "platform")),
        description=updated.get("description"),
        permissions=updated.get("permissions", []) or [],
        status=updated.get("status", "active"),
        is_system=bool(updated.get("is_system", False)),
        created_by_user_id=updated.get("created_by_user_id", ""),
    )


@router.post("/users", response_model=PlatformUserResponse, status_code=status.HTTP_201_CREATED)
async def create_platform_user(
    payload: PlatformUserCreateRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    require_admin_role(current_user)
    await _require_platform_permission(current_user, ["users:write"])
    current_user_id = str(current_user.get("id", ""))
    idempotency_scope = f"platform:create-user:{current_user_id}"
    normalized_idempotency_key = (idempotency_key or "").strip() or None
    if normalized_idempotency_key:
        precheck = await postgres_store.acquire_idempotency_key(
            scope=idempotency_scope,
            key=normalized_idempotency_key,
            request_hash=_request_hash(payload.model_dump(mode="json", exclude_none=False)),
            user_id=current_user_id,
        )
        precheck_state = str(precheck.get("state", "")).lower()
        if precheck_state == "completed":
            record = precheck.get("record") or {}
            cached_payload = record.get("response_payload")
            if cached_payload is not None:
                return cached_payload
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key already completed but response is unavailable",
            )
        if precheck_state == "conflict":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key was already used with a different request payload",
            )
        if precheck_state in {"in_progress", "failed"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Request with this idempotency key is not reusable, please use a new key",
            )

    try:
        normalized_email = str(payload.email).strip().lower()
        existing_user = await auth_service.get_user_by_email(normalized_email)
        if existing_user:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

        platform_role_id = payload.platform_role_id
        if platform_role_id:
            role_doc = await postgres_store.get_platform_role(platform_role_id)
            if not role_doc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Platform role not found")
            if role_doc.get("status") != "active":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Platform role is inactive")

        user_doc = await auth_service.create_user(
            UserCreate(
                email=normalized_email,
                username=payload.username.strip(),
                password=payload.password,
                first_name=payload.first_name.strip(),
                last_name=payload.last_name.strip(),
                role=UserRole.USER,
                tenant_id=None,
            )
        )

        await org_service.ensure_platform_org_exists()
        await org_service.create_membership(
            org_id=PLATFORM_ORG_ID,
            user_id=str(user_doc.get("id", "")),
            role=payload.membership_role,
            status="active",
        )

        allowed_app_ids = normalize_allowed_app_ids(payload.allowed_app_ids) or ["users"]
        default_app_id = payload.default_app_id if payload.default_app_id in allowed_app_ids else allowed_app_ids[0]

        update_data: Dict[str, Any] = {
            "allowed_app_ids": allowed_app_ids,
            "default_app_id": default_app_id,
            "platform_role_id": platform_role_id,
            "active_org_id": PLATFORM_ORG_ID,
        }
        updated_user = await auth_service.update_user_fields(str(user_doc.get("id", "")), update_data)
        response_payload = _to_platform_user_response(updated_user)
        await audit_service.log_event(
            actor=current_user,
            action="platform.user.create",
            resource_type="user",
            resource_id=str(updated_user.get("id", "")),
            metadata={
                "email": payload.email,
                "platform_role_id": platform_role_id,
                "membership_role": payload.membership_role,
                "allowed_app_ids": allowed_app_ids,
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
        if normalized_idempotency_key:
            await postgres_store.fail_idempotency_key(
                scope=idempotency_scope,
                key=normalized_idempotency_key,
                error_message=exc.detail if isinstance(exc.detail, str) else "Request failed",
            )
        raise
    except Exception as exc:
        if normalized_idempotency_key:
            await postgres_store.fail_idempotency_key(
                scope=idempotency_scope,
                key=normalized_idempotency_key,
                error_message=str(exc),
            )
        raise


@router.put("/users/{user_id}/platform-role", response_model=PlatformUserResponse)
async def assign_platform_role_to_user(
    user_id: str,
    payload: PlatformUserRoleAssignRequest,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    require_admin_role(current_user)
    await _require_platform_permission(current_user, ["users:write"])
    target_user = await auth_service.get_user(user_id)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    role_doc = await postgres_store.get_platform_role(payload.platform_role_id)
    if not role_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Platform role not found")
    if role_doc.get("status") != "active":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Platform role is inactive")

    updated_user = await auth_service.update_user_fields(user_id, {"platform_role_id": payload.platform_role_id})
    await audit_service.log_event(
        actor=current_user,
        action="platform.user.role.assign",
        resource_type="user",
        resource_id=user_id,
        metadata={"platform_role_id": payload.platform_role_id},
    )
    return _to_platform_user_response(updated_user)


@router.put("/active-org", response_model=ActiveOrgUpdateResponse)
async def set_active_org(
    payload: ActiveOrgUpdateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    memberships = current_user.get("org_memberships") or []
    if org_service.is_platform_staff(memberships) and payload.org_id != PLATFORM_ORG_ID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Platform admins are fixed to MindRind org context",
        )

    has_access = await org_service.user_can_access_org(current_user, payload.org_id)
    if not has_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization access denied")

    target_org = await postgres_store.get_org(payload.org_id)
    if not target_org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    target_status = str(target_org.get("status", "active")).strip().lower()
    if target_status in {"inactive", "suspended"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot switch to an inactive or suspended organization",
        )

    await auth_service.update_user_fields(str(current_user.get("id", "")), {"active_org_id": payload.org_id})
    await audit_service.log_event(
        actor=current_user,
        action="platform.user.active_org.set",
        resource_type="user",
        resource_id=str(current_user.get("id", "")),
        metadata={"active_org_id": payload.org_id},
    )
    return ActiveOrgUpdateResponse(user_id=str(current_user.get("id", "")), active_org_id=payload.org_id)
