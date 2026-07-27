"""
Unified Voice Agent API Router
Handles both browser testing and phone call sessions using a single service.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from twilio.request_validator import RequestValidator

from app.api.v1.routers.auth import get_current_user_from_token, require_admin_role
from app.core import config
from app.core.encryption import encryption_service
from app.core.security import SecurityService
from app.services.store import store
from app.services.unified_voice_agent import unified_voice_agent_service

# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice-agent", tags=["Voice Agent"])


def get_security_service() -> SecurityService:
    """Get security service instance."""
    try:
        return SecurityService()
    except Exception as e:
        logger.warning(f"Security service unavailable: {e}")
        return None


def get_client_ip(request: Request) -> str:
    """Get client IP address from request."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host
    return "unknown"


# Request/Response Models


class StartSessionRequest(BaseModel):
    """Request model for starting a voice agent session."""

    tenant_id: str = Field(..., description="Tenant identifier")
    service_type: str = Field(..., description="Service type (e.g., 'Plumbing', 'Electrician')")
    test_mode: bool = Field(True, description="True for browser testing, False for phone calls")
    phone_number: Optional[str] = Field(None, description="Required if test_mode=False")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional session metadata")


class StartSessionResponse(BaseModel):
    """Response model for session start."""

    session_id: str
    room_name: str
    status: str
    message: str
    # Browser testing fields
    token: Optional[str] = None
    livekit_url: Optional[str] = None
    websocket_url: Optional[str] = None
    # Phone call fields
    twilio_call_sid: Optional[str] = None
    phone_number: Optional[str] = None


class SessionStatusResponse(BaseModel):
    """Response model for session status."""

    session_id: str
    tenant_id: str
    service_type: str
    room_name: str
    status: str
    test_mode: bool
    connection_type: str
    created_at: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


# API Endpoints


@router.post("/start-session", response_model=StartSessionResponse)
async def start_session(request: StartSessionRequest, http_request: Request):
    """
    Start a voice agent session.

    **Test Mode (test_mode=true)**:
    - Returns LiveKit room token for browser testing
    - No phone call is made
    - Connect your browser directly to LiveKit room

    **Live Mode (test_mode=false)**:
    - Initiates a real phone call via Twilio
    - Phone call is bridged to LiveKit room
    - Requires phone_number parameter
    - Uses tenant's Twilio credentials
    """
    # Rate limiting: 20 requests per minute per IP
    # Note: For user-based rate limiting, we'd need to extract user_id from auth token
    security_service = get_security_service()
    if security_service:
        client_ip = get_client_ip(http_request)
        await security_service.enforce_rate_limit(client_ip, limit=20, window_seconds=60, operation="voice_start_session")

    try:
        # Validate request
        if not request.test_mode and not request.phone_number:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="phone_number is required when test_mode=false"
            )

        # Start session
        result = await unified_voice_agent_service.start_session(
            tenant_id=request.tenant_id,
            service_type=request.service_type,
            test_mode=request.test_mode,
            phone_number=request.phone_number,
            metadata=request.metadata,
        )

        return result

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to start session: {str(e)}")


@router.post("/end-session/{session_id}")
async def end_session(session_id: str):
    """
    End a voice agent session.

    This will:
    - Close the LiveKit room
    - Hang up any active phone calls
    - Clean up session resources
    """
    try:
        success = await unified_voice_agent_service.end_session(session_id)

        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

        return {"session_id": session_id, "status": "ended", "message": "Session ended successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to end session: {str(e)}")


@router.get("/session-status/{session_id}", response_model=SessionStatusResponse)
async def get_session_status(session_id: str):
    """
    Get the status of a voice agent session.

    Returns detailed information about the session including:
    - Current status
    - Connection type (browser or phone)
    - Timestamps
    - Room information
    """
    try:
        status_data = await unified_voice_agent_service.get_session_status(session_id)

        if not status_data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found")

        return status_data

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get session status: {str(e)}"
        )


@router.get("/tenant/{tenant_id}/sessions")
async def get_tenant_sessions(tenant_id: str, active_only: bool = True):
    """
    Get all sessions for a specific tenant.

    Args:
        tenant_id: Tenant identifier
        active_only: If True, returns only active sessions. If False, returns all sessions.
    """
    try:
        sessions = await unified_voice_agent_service.list_tenant_sessions(tenant_id=tenant_id, active_only=active_only)
        return {"tenant_id": tenant_id, "sessions": sessions, "count": len(sessions)}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get tenant sessions: {str(e)}"
        )


@router.get("/tenant/{tenant_id}/agent-stats")
async def get_tenant_agent_stats(tenant_id: str):
    """
    Get statistics about the tenant's voice agent.

    Returns information about:
    - Agent instances
    - Active sessions
    - Total sessions
    """
    try:
        return await unified_voice_agent_service.get_tenant_agent_stats(tenant_id=tenant_id)

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get agent stats: {str(e)}")


# Twilio Webhook Endpoints


async def _validate_twilio_signature(request: Request, form_data: Dict[str, Any]) -> bool:
    """
    Validate Twilio webhook signature to ensure request is from Twilio.

    Returns True if signature is valid, False otherwise.
    """
    try:
        # Get signature from headers
        signature = request.headers.get("X-Twilio-Signature")
        if not signature:
            logger.warning("Missing X-Twilio-Signature header in webhook request")
            return False

        # Get AccountSid from form data (Twilio sends this)
        account_sid = form_data.get("AccountSid")
        if not account_sid:
            logger.warning("Missing AccountSid in webhook request")
            return False

        # Try to get auth_token from integration
        auth_token = None

        # First, try to find integration by account_sid
        # Note: This requires querying all integrations - may need optimization
        try:
            # Get all integrations and find matching account_sid
            # For now, we'll try to get by phone number (To field) as fallback
            to_number = form_data.get("To")
            if to_number:
                phone_record = await store.get_phone_by_number(to_number)
                if phone_record and phone_record.get("tenant_id"):
                    integration = await store.get_twilio_integration(phone_record["tenant_id"])
                    if integration and integration.get("account_sid") == account_sid:
                        encrypted_token = integration.get("auth_token")
                        if encrypted_token:
                            auth_token = encryption_service.decrypt(encrypted_token)
        except Exception as e:
            logger.warning(f"Could not get auth_token from integration: {e}")

        # Fallback to system Twilio credentials if available
        if not auth_token:
            if config.TWILIO_ACCOUNT_SID == account_sid and config.TWILIO_AUTH_TOKEN:
                auth_token = config.TWILIO_AUTH_TOKEN
                logger.info("Using system Twilio credentials for signature validation")

        if not auth_token:
            logger.error(f"Could not find auth_token for AccountSid: {account_sid}")
            return False

        # Build full URL for validation (Twilio requires this)
        # Twilio signature validation requires the full URL as received by the server
        # FastAPI's request.url includes the full URL with scheme and host
        url = str(request.url)
        # Remove query string if present (Twilio validates against URL without query params)
        if "?" in url:
            url = url.split("?")[0]

        # Convert form_data to dict of strings for validation
        params = {k: str(v) for k, v in form_data.items()}

        # Validate signature
        validator = RequestValidator(auth_token)
        is_valid = validator.validate(url, params, signature)

        if not is_valid:
            logger.warning(f"Invalid Twilio signature for AccountSid: {account_sid}")

        return is_valid

    except Exception as e:
        logger.error(f"Error validating Twilio signature: {e}", exc_info=True)
        return False


@router.post("/twilio/webhook")
async def twilio_webhook(request: Request):
    """
    Twilio webhook endpoint.

    This is called by Twilio when a call is initiated.
    Returns TwiML that connects the call to LiveKit room via SIP.

    Security: Validates X-Twilio-Signature header to ensure request is from Twilio.
    """
    # Rate limiting: 100 requests per minute per IP (higher for legitimate webhook traffic)
    security_service = get_security_service()
    if security_service:
        client_ip = get_client_ip(request)
        await security_service.enforce_rate_limit(client_ip, limit=100, window_seconds=60, operation="twilio_webhook")

    try:
        form_data = await request.form()
        form_dict = dict(form_data)

        # Validate Twilio signature for security
        if not await _validate_twilio_signature(request, form_dict):
            logger.error("Invalid Twilio signature - rejecting webhook request")
            # Return error TwiML instead of HTTP error (Twilio expects TwiML)
            from twilio.twiml.voice_response import VoiceResponse

            error_response = VoiceResponse()
            error_response.say("Authentication error. Please contact support.")
            error_response.hangup()
            return Response(content=str(error_response), media_type="application/xml", status_code=403)

        logger.info("Twilio signature validated successfully")

        response = await unified_voice_agent_service.handle_twilio_webhook(form_dict)

        return Response(content=str(response), media_type="application/xml")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in Twilio webhook: {e}", exc_info=True)
        # Return error TwiML
        from twilio.twiml.voice_response import VoiceResponse

        error_response = VoiceResponse()
        error_response.say("An application error occurred. Please try again later.")
        error_response.hangup()
        return Response(content=str(error_response), media_type="application/xml")


@router.post("/twilio/status")
async def twilio_status_callback(request: Request):
    """
    Twilio status callback endpoint.

    This is called by Twilio to report call status changes.

    Security: Validates X-Twilio-Signature header to ensure request is from Twilio.
    """
    # Rate limiting: 100 requests per minute per IP (higher for legitimate webhook traffic)
    security_service = get_security_service()
    if security_service:
        client_ip = get_client_ip(request)
        await security_service.enforce_rate_limit(client_ip, limit=100, window_seconds=60, operation="twilio_status")

    try:
        form_data = await request.form()
        form_dict = dict(form_data)

        # Validate Twilio signature for security
        if not await _validate_twilio_signature(request, form_dict):
            logger.error("Invalid Twilio signature - rejecting status callback request")
            return Response(status_code=403, content="Invalid signature")

        logger.info("Twilio signature validated successfully for status callback")

        await unified_voice_agent_service.handle_twilio_status_callback(form_dict)

        return Response(status_code=200)

    except Exception as e:
        logger.error(f"Error in Twilio status callback: {e}", exc_info=True)
        return Response(status_code=500)


# Cache Management Endpoints


@router.delete("/cache/clear")
async def clear_agent_cache(
    tenant_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    current_user: Dict = Depends(get_current_user_from_token),
):
    """
    Clear cached agent instances.

    Use this endpoint if you've updated agent settings (voice, greeting, etc.)
    and want to force the system to recreate the agent with new settings.

    - **tenant_id** (optional): Clear only agents for this tenant
    - **agent_id** (optional): Clear only this specific agent
    - If no parameters provided, clears ALL cached agents
    """
    try:
        require_admin_role(current_user)
        result = await unified_voice_agent_service.clear_agent_cache(tenant_id=tenant_id, agent_id=agent_id)
        return result

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to clear cache: {str(e)}")
