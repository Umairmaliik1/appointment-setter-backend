"""
Twilio Integration API routes for managing user's Twilio credentials and phone numbers.
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.routers.auth import get_current_user_from_token, require_admin_role, verify_tenant_access
from app.api.v1.schemas.voice_agent import TwilioCredentialTest, TwilioIntegrationConfig
from app.api.v1.services.twilio_integration import twilio_integration_service
from app.core import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/twilio-integration", tags=["twilio-integration"])


@router.post("/test-credentials")
async def test_twilio_credentials(
    credentials: TwilioCredentialTest, current_user: Dict = Depends(get_current_user_from_token)
):
    """Test Twilio credentials and return account information."""
    try:
        result = await twilio_integration_service.test_credentials(credentials)

        if not result.success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.message)

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to test credentials: {str(e)}")


@router.post("/tenant/{tenant_id}/create")
async def create_twilio_integration(
    tenant_id: str, config: TwilioIntegrationConfig, current_user: Dict = Depends(get_current_user_from_token)
):
    """Create Twilio integration for a tenant."""
    try:
        verify_tenant_access(current_user, tenant_id)
        result = await twilio_integration_service.create_integration(tenant_id, config)

        return {"message": "Twilio integration created successfully", "integration": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating Twilio integration: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create integration: {str(e)}"
        )


@router.put("/tenant/{tenant_id}/update")
async def update_twilio_integration(
    tenant_id: str, config: TwilioIntegrationConfig, current_user: Dict = Depends(get_current_user_from_token)
):
    """Update Twilio integration for a tenant."""
    try:
        verify_tenant_access(current_user, tenant_id)
        result = await twilio_integration_service.update_integration(tenant_id, config)

        return {"message": "Twilio integration updated successfully", "integration": result}

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/tenant/{tenant_id}")
async def get_twilio_integration(tenant_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    """Get Twilio integration for a tenant."""
    try:
        verify_tenant_access(current_user, tenant_id)
        integration = await twilio_integration_service.get_integration(tenant_id)

        if not integration:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Twilio integration not found")

        # Don't return sensitive data like auth_token
        safe_integration = {
            "id": integration.get("id"),
            "tenant_id": integration.get("tenant_id"),
            "account_sid": integration.get("account_sid"),
            "phone_number": integration.get("phone_number"),
            "webhook_url": integration.get("webhook_url"),
            "status_callback_url": integration.get("status_callback_url"),
            "account_info": integration.get("account_info"),
            "status": integration.get("status"),
            "created_at": integration.get("created_at"),
            "updated_at": integration.get("updated_at"),
        }

        return safe_integration

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get integration: {str(e)}")


@router.delete("/tenant/{tenant_id}")
async def delete_twilio_integration(tenant_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    """Delete Twilio integration for a tenant."""
    try:
        verify_tenant_access(current_user, tenant_id)
        success = await twilio_integration_service.delete_integration(tenant_id)

        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Twilio integration not found")

        return {"message": "Twilio integration deleted successfully"}

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete integration: {str(e)}"
        )


@router.post("/account/{account_sid}/phone-numbers")
async def get_available_phone_numbers(
    account_sid: str, credentials: TwilioCredentialTest, current_user: Dict = Depends(get_current_user_from_token)
):
    """Get available phone numbers from Twilio account."""
    try:
        require_admin_role(current_user)
        # Verify credentials first
        test_result = await twilio_integration_service.test_credentials(credentials)

        if not test_result.success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=test_result.message)

        # Get phone numbers
        phone_numbers = await twilio_integration_service.get_available_phone_numbers(
            credentials.account_sid, credentials.auth_token
        )

        return {"phone_numbers": phone_numbers, "total": len(phone_numbers)}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get phone numbers: {str(e)}")


@router.post("/validate-webhook")
async def validate_webhook_url(webhook_data: Dict[str, str], current_user: Dict = Depends(get_current_user_from_token)):
    """Validate webhook URL format and accessibility."""
    try:
        webhook_url = webhook_data.get("webhook_url")

        if not webhook_url:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook URL is required")

        is_valid = await twilio_integration_service.validate_webhook_url(webhook_url)

        return {
            "webhook_url": webhook_url,
            "is_valid": is_valid,
            "message": "Webhook URL is valid" if is_valid else "Webhook URL is invalid or not accessible",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to validate webhook: {str(e)}")


@router.get("/tenant/{tenant_id}/unassigned")
async def list_unassigned_numbers_for_tenant(
    tenant_id: str, current_user: Dict = Depends(get_current_user_from_token)
):
    """List Twilio numbers owned by tenant's Twilio account that are not assigned in our DB."""
    try:
        verify_tenant_access(current_user, tenant_id)
        integration = await twilio_integration_service.get_integration(tenant_id)
        if not integration:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Twilio integration not configured for this tenant"
            )

        numbers = await twilio_integration_service.get_unassigned_numbers_for_tenant(
            tenant_id=tenant_id,
            account_sid=integration["account_sid"],
            auth_token=integration["auth_token"],
        )
        return {"phone_numbers": numbers, "total": len(numbers)}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to list unassigned numbers: {str(e)}"
        )


@router.get("/system/unassigned")
async def list_unassigned_numbers_from_system(
    tenant_id: str, current_user: Dict = Depends(get_current_user_from_token)
):
    """List numbers from system Twilio account not present in any tenant pool (best-effort)."""
    try:
        require_admin_role(current_user)
        if not config.TWILIO_ACCOUNT_SID or not config.TWILIO_AUTH_TOKEN:
            raise HTTPException(status_code=400, detail="System Twilio credentials not configured")

        owned = await twilio_integration_service.get_owned_numbers(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)

        # We will not exclude globally assigned numbers due to lack of global list here; return owned as-is
        return {"phone_numbers": owned, "total": len(owned)}
    except Exception as e:
        # If Twilio auth fails, surface a 401-style error to user
        if "Authenticate" in str(e) or "401" in str(e):
            raise HTTPException(
                status_code=401, detail="Twilio authentication failed. Check TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN."
            )
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to list system numbers: {str(e)}"
        )


@router.post("/search-available")
async def search_available_numbers(payload: Dict[str, Any], current_user: Dict = Depends(get_current_user_from_token)):
    """Search available numbers using either tenant or system credentials.

    Expected payload: {
        "mode": "tenant" | "system",
        "tenant_id": "..." (required for tenant mode),
        "country": "US",
        "number_type": "local" | "tollfree",
        "area_code": "optional"
    }
    """
    try:
        mode = payload.get("mode", "tenant")
        tenant_id = payload.get("tenant_id")
        country = payload.get("country", "US")
        number_type = payload.get("number_type", "local")
        area_code = payload.get("area_code")

        if mode == "tenant":
            if not tenant_id:
                raise HTTPException(status_code=400, detail="tenant_id is required for tenant mode")
            verify_tenant_access(current_user, tenant_id)
            integration = await twilio_integration_service.get_integration(tenant_id)
            if not integration:
                raise HTTPException(status_code=400, detail="Twilio integration not configured for this tenant")
            account_sid = integration["account_sid"]
            auth_token = integration["auth_token"]
        else:
            require_admin_role(current_user)
            if not config.TWILIO_ACCOUNT_SID or not config.TWILIO_AUTH_TOKEN:
                raise HTTPException(status_code=400, detail="System Twilio credentials not configured")
            account_sid = config.TWILIO_ACCOUNT_SID
            auth_token = config.TWILIO_AUTH_TOKEN

        results = await twilio_integration_service.search_available_numbers(
            account_sid=account_sid, auth_token=auth_token, country=country, number_type=number_type, area_code=area_code
        )
        return {"available_numbers": results, "total": len(results)}
    except Exception as e:
        if "Authenticate" in str(e) or "401" in str(e):
            raise HTTPException(status_code=401, detail="Twilio authentication failed. Verify credentials.")
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to search numbers: {str(e)}")


@router.post("/tenant/{tenant_id}/purchase")
async def purchase_number_for_tenant(
    tenant_id: str, payload: Dict[str, Any], current_user: Dict = Depends(get_current_user_from_token)
):
    """Purchase a number using tenant's Twilio credentials and add to tenant pool.

    Payload: { "phone_number": "+1...", "webhook_url"?: str, "status_callback_url"?: str }
    """
    try:
        verify_tenant_access(current_user, tenant_id)
        phone_number = payload.get("phone_number")
        if not phone_number:
            raise HTTPException(status_code=400, detail="phone_number is required")

        integration = await twilio_integration_service.get_integration(tenant_id)
        if not integration:
            raise HTTPException(status_code=400, detail="Twilio integration not configured for this tenant")

        created = await twilio_integration_service.purchase_phone_number(
            account_sid=integration["account_sid"],
            auth_token=integration["auth_token"],
            tenant_id=tenant_id,
            phone_number=phone_number,
            webhook_url=payload.get("webhook_url"),
            status_callback_url=payload.get("status_callback_url"),
        )

        if not created:
            raise HTTPException(status_code=500, detail="Failed to purchase phone number")
        return {"message": "Phone number purchased and added to pool", "phone": created}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to purchase number: {str(e)}")


@router.post("/system/purchase")
async def purchase_number_from_system(payload: Dict[str, Any], current_user: Dict = Depends(get_current_user_from_token)):
    """Purchase a number using system credentials and add to a tenant's pool.

    Payload: { "tenant_id": str, "phone_number": "+1...", "webhook_url"?: str, "status_callback_url"?: str }
    """
    try:
        require_admin_role(current_user)
        if not config.TWILIO_ACCOUNT_SID or not config.TWILIO_AUTH_TOKEN:
            raise HTTPException(status_code=400, detail="System Twilio credentials not configured")

        tenant_id = payload.get("tenant_id")
        phone_number = payload.get("phone_number")
        if not tenant_id or not phone_number:
            raise HTTPException(status_code=400, detail="tenant_id and phone_number are required")

        created = await twilio_integration_service.purchase_phone_number(
            account_sid=config.TWILIO_ACCOUNT_SID,
            auth_token=config.TWILIO_AUTH_TOKEN,
            tenant_id=tenant_id,
            phone_number=phone_number,
            webhook_url=payload.get("webhook_url"),
            status_callback_url=payload.get("status_callback_url"),
            is_system_purchase=True,  # System purchases don't require tenant integration
        )

        if not created:
            raise HTTPException(status_code=500, detail="Failed to purchase phone number")
        return {"message": "Phone number purchased from system account and added to pool", "phone": created}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to purchase number: {str(e)}")
