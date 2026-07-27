"""
Tenant management API routes using PostgreSQL.
"""

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.routers.auth import get_current_user_from_token, require_admin_role, require_app_access, verify_tenant_access
from app.services.org_service import org_service
from app.api.v1.schemas.tenant import (
    AgentSettingsCreate,
    BusinessInfoCreate,
    TenantCreate,
    TenantResponse,
    TenantUpdate,
)
from app.api.v1.services.tenant import tenant_service

router = APIRouter(prefix="/tenants", tags=["tenants"], dependencies=[Depends(require_app_access("appointment_setter"))])


# Match login endpoint pattern - use specific path without trailing slash
# This avoids redirects and matches how /api/v1/auth/login works
@router.post("", response_model=TenantResponse)
async def create_tenant(tenant_data: TenantCreate, current_user: Dict = Depends(get_current_user_from_token)):
    """Create a new tenant. Requires admin role.

    The creating user becomes the tenant's owner: they are recorded as
    `created_by_user_id` and added as an admin member of the tenant's
    customer org. Other admins do NOT automatically see this tenant — only
    the creator and platform staff do. Anything created under the tenant
    (voice agents, phone numbers, appointments) inherits the same scoping.
    """
    require_admin_role(current_user)
    try:
        tenant = await tenant_service.create_tenant(
            tenant_data,
            creator_user_id=str(current_user.get("id") or "") or None,
        )

        from app.core.response_mappers import to_tenant_response

        return to_tenant_response(tenant)

    except ValueError as e:
        # Duplicate tenant (same name)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create tenant: {str(e)}")


# Match login endpoint pattern - use specific path without trailing slash
@router.get("", response_model=List[TenantResponse])
async def list_tenants(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: Dict = Depends(get_current_user_from_token),
):
    """List tenants the caller has access to.

    Scoping rule:
      * Platform staff (the SaaS operators) see every tenant.
      * Everyone else — including tenant-level admins — sees only the
        tenants they have a customer-org membership for, i.e. the tenants
        they created (or were explicitly added to). The `role == "admin"`
        short-circuit was removed: a customer-org admin is the admin of
        THEIR tenant, not of every other customer's tenant.
    """
    try:
        from app.core.response_mappers import to_tenant_response

        memberships = current_user.get("org_memberships") or []
        if current_user.get("platform_scope") or org_service.is_platform_staff(memberships):
            tenants = await tenant_service.list_tenants(limit, offset)
            return [to_tenant_response(tenant) for tenant in tenants]

        accessible_tenant_ids = [
            str(t) for t in (current_user.get("accessible_tenant_ids") or []) if t
        ]
        # Back-compat for users not migrated to org memberships yet.
        legacy_tenant_id = current_user.get("tenant_id")
        if legacy_tenant_id and str(legacy_tenant_id) not in accessible_tenant_ids:
            accessible_tenant_ids.append(str(legacy_tenant_id))

        if not accessible_tenant_ids:
            return []

        # Pagination over the already-scoped id list (small per user).
        page_ids = accessible_tenant_ids[offset : offset + limit]
        results = []
        for tid in page_ids:
            tenant = await tenant_service.get_tenant(tid)
            if tenant:
                results.append(to_tenant_response(tenant))
        return results

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to list tenants: {str(e)}")


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(tenant_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    """Get tenant by ID."""
    verify_tenant_access(current_user, tenant_id)
    try:
        tenant = await tenant_service.get_tenant(tenant_id)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

        from app.core.response_mappers import to_tenant_response

        return to_tenant_response(tenant)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get tenant: {str(e)}")


@router.put("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(tenant_id: str, tenant_data: TenantUpdate, current_user: Dict = Depends(get_current_user_from_token)):
    """Update tenant."""
    verify_tenant_access(current_user, tenant_id)
    try:
        tenant = await tenant_service.update_tenant(tenant_id, tenant_data)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

        return TenantResponse(
            id=tenant["id"],
            name=tenant["name"],
            owner_email=tenant.get("owner_email"),
            created_at=tenant["created_at"],
            updated_at=tenant["updated_at"],
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update tenant: {str(e)}")


@router.post("/{tenant_id}/business-info")
async def create_business_info(
    tenant_id: str, business_data: BusinessInfoCreate, current_user: Dict = Depends(get_current_user_from_token)
):
    """Create business information for a tenant."""
    verify_tenant_access(current_user, tenant_id)
    try:
        business_info = await tenant_service.create_business_config(tenant_id, business_data)

        return {"message": "Business information created successfully", "business_info": business_info}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create business info: {str(e)}"
        )


@router.get("/{tenant_id}/business-info")
async def get_business_info(tenant_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    """Get business information for a tenant."""
    verify_tenant_access(current_user, tenant_id)
    try:
        business_info = await tenant_service.get_business_config(tenant_id)
        if not business_info:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business information not found")

        return business_info

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get business info: {str(e)}")


@router.put("/{tenant_id}/business-info")
async def update_business_info(
    tenant_id: str, business_data: BusinessInfoCreate, current_user: Dict = Depends(get_current_user_from_token)
):
    """Update business information for a tenant."""
    verify_tenant_access(current_user, tenant_id)
    try:
        business_info = await tenant_service.update_business_config(tenant_id, business_data)
        if not business_info:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business information not found")

        return {"message": "Business information updated successfully", "business_info": business_info}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update business info: {str(e)}"
        )


@router.post("/{tenant_id}/agent-settings")
async def create_agent_settings(
    tenant_id: str, agent_data: AgentSettingsCreate, current_user: Dict = Depends(get_current_user_from_token)
):
    """Create agent settings for a tenant."""
    verify_tenant_access(current_user, tenant_id)
    try:
        agent_settings = await tenant_service.create_agent_settings(tenant_id, agent_data)

        return {"message": "Agent settings created successfully", "agent_settings": agent_settings}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create agent settings: {str(e)}"
        )


@router.get("/{tenant_id}/agent-settings")
async def get_agent_settings(tenant_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    """Get agent settings for a tenant."""
    verify_tenant_access(current_user, tenant_id)
    try:
        agent_settings = await tenant_service.get_agent_settings(tenant_id)
        if not agent_settings:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent settings not found")

        return agent_settings

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get agent settings: {str(e)}"
        )


@router.put("/{tenant_id}/agent-settings")
async def update_agent_settings(
    tenant_id: str, agent_data: AgentSettingsCreate, current_user: Dict = Depends(get_current_user_from_token)
):
    """Update agent settings for a tenant."""
    verify_tenant_access(current_user, tenant_id)
    try:
        agent_settings = await tenant_service.update_agent_settings(tenant_id, agent_data)
        if not agent_settings:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent settings not found")

        return {"message": "Agent settings updated successfully", "agent_settings": agent_settings}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update agent settings: {str(e)}"
        )


# Note: Twilio integration endpoints have been moved to /api/v1/twilio-integration
# Use the dedicated twilio_integration router for all Twilio operations



