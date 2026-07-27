"""
Response mapping helpers for converting dict data to Pydantic response models.
Centralizes timestamp parsing and response model construction.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from app.api.v1.schemas.agent import AgentResponse
from app.api.v1.schemas.auth import UserResponse
from app.api.v1.schemas.phone_number import PhoneNumberResponse
from app.api.v1.schemas.tenant import TenantResponse

# Configure logging
logger = logging.getLogger(__name__)


def parse_iso_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """
    Parse ISO timestamp string to datetime object.
    Handles both formats: with "Z" suffix and without.

    Args:
        ts: ISO timestamp string (e.g., "2024-01-01T00:00:00Z" or "2024-01-01T00:00:00+00:00")

    Returns:
        datetime object or None if input is None/empty
    """
    if not ts:
        return None

    try:
        # Replace "Z" with "+00:00" for timezone-aware parsing
        normalized_ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized_ts)
    except (ValueError, AttributeError) as e:
        logger.warning(f"Failed to parse timestamp '{ts}': {e}")
        return None


def to_user_response(user_dict: Dict[str, Any]) -> UserResponse:
    """
    Convert user dictionary to UserResponse model.
    Handles timestamp parsing and UUID conversion.
    """
    try:
        # Parse timestamps
        created_at = parse_iso_timestamp(user_dict.get("created_at"))
        updated_at = parse_iso_timestamp(user_dict.get("updated_at"))
        last_login = parse_iso_timestamp(user_dict.get("last_login"))

        # Convert UUID strings to UUID objects
        user_id = user_dict.get("id")
        if isinstance(user_id, str):
            user_id = UUID(user_id)

        tenant_id = user_dict.get("tenant_id")
        if tenant_id and isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)

        return UserResponse(
            id=user_id,
            email=user_dict.get("email", ""),
            username=user_dict.get("username", ""),
            first_name=user_dict.get("first_name", ""),
            last_name=user_dict.get("last_name", ""),
            role=user_dict.get("role", "user"),
            status=user_dict.get("status", "active"),
            tenant_id=tenant_id,
            platform_role_id=user_dict.get("platform_role_id"),
            allowed_app_ids=user_dict.get("allowed_app_ids", []) or [],
            default_app_id=user_dict.get("default_app_id"),
            org_memberships=user_dict.get("org_memberships", []) or [],
            active_org_id=user_dict.get("active_org_id"),
            is_email_verified=user_dict.get("is_email_verified", False),
            last_login=last_login,
            created_at=created_at or datetime.now(timezone.utc),
            updated_at=updated_at or datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.error(f"Error converting user dict to UserResponse: {e}", exc_info=True)
        raise


def to_tenant_response(tenant_dict: Dict[str, Any]) -> TenantResponse:
    """
    Convert tenant dictionary to TenantResponse model.
    Handles timestamp parsing and UUID conversion.
    """
    try:
        # Parse timestamps
        created_at = parse_iso_timestamp(tenant_dict.get("created_at"))
        updated_at = parse_iso_timestamp(tenant_dict.get("updated_at"))

        # Convert UUID strings to UUID objects
        tenant_id = tenant_dict.get("id")
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)

        return TenantResponse(
            id=tenant_id,
            name=tenant_dict.get("name", ""),
            owner_email=tenant_dict.get("owner_email"),
            created_at=created_at or datetime.now(timezone.utc),
            updated_at=updated_at or datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.error(f"Error converting tenant dict to TenantResponse: {e}", exc_info=True)
        raise


def to_agent_response(agent_dict: Dict[str, Any]) -> AgentResponse:
    """
    Convert agent dictionary to AgentResponse model.
    Note: AgentResponse uses string timestamps, not datetime objects.
    """
    try:
        return AgentResponse(
            id=agent_dict.get("id", ""),
            agent_type=agent_dict.get("agent_type") or "voice",
            tenant_id=agent_dict.get("tenant_id", ""),
            name=agent_dict.get("name", ""),
            voice_id=agent_dict.get("voice_id", ""),
            language=agent_dict.get("language", "en"),
            greeting_message=agent_dict.get("greeting_message", ""),
            service_type=agent_dict.get("service_type", ""),
            system_prompt=agent_dict.get("system_prompt", "") or "",
            status=agent_dict.get("status", "inactive"),
            created_at=agent_dict.get("created_at", ""),
            updated_at=agent_dict.get("updated_at", ""),
        )
    except Exception as e:
        logger.error(f"Error converting agent dict to AgentResponse: {e}", exc_info=True)
        raise


def to_phone_number_response(phone_dict: Dict[str, Any]) -> PhoneNumberResponse:
    """
    Convert phone number dictionary to PhoneNumberResponse model.
    """
    try:
        return PhoneNumberResponse(
            id=phone_dict.get("id", ""),
            tenant_id=phone_dict.get("tenant_id", ""),
            phone_number=phone_dict.get("phone_number", ""),
            agent_id=phone_dict.get("agent_id", ""),
            agent_name=phone_dict.get("agent_name"),
            twilio_integration_id=phone_dict.get("twilio_integration_id", ""),
            status=phone_dict.get("status", "inactive"),
            usage_role=phone_dict.get("usage_role") or "voice_agent_inbound",
            role_status=phone_dict.get("role_status") or "active",
            conflict_code=phone_dict.get("conflict_code"),
            conflict_message=phone_dict.get("conflict_message"),
            created_at=phone_dict.get("created_at", ""),
            updated_at=phone_dict.get("updated_at", ""),
        )
    except Exception as e:
        logger.error(f"Error converting phone dict to PhoneNumberResponse: {e}", exc_info=True)
        raise
