"""SMS App API routes: leads, campaigns, inbox, suppressions, and Twilio webhooks."""

import asyncio
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status

from app.api.v1.routers.auth import get_current_user_from_token, require_app_access, verify_tenant_access
from app.api.v1.services.auth import auth_service
from app.api.v1.services.sms import sms_inbox_channel
from app.core.async_redis import async_redis_client
from app.core.platform_apps import has_app_access
from app.api.v1.schemas.sms import (
    SmsCampaignCreate,
    SmsCampaignResponse,
    SmsCampaignUpdate,
    SmsConversationDetail,
    SmsConversationResponse,
    SmsLeadBulkImport,
    SmsLeadCreate,
    SmsLeadResponse,
    SmsSendNow,
    SmsSuppressionCreate,
    SmsSuppressionResponse,
)
from app.api.v1.services.sms import (
    sms_campaign_service,
    sms_inbox_service,
    sms_lead_service,
    sms_number_service,
    sms_suppression_service,
    sms_webhook_service,
)
from app.core.security import SecurityService
from app.core.twilio_webhook import validate_twilio_signature

logger = logging.getLogger(__name__)

# App-gated router for authenticated tenant operations.
router = APIRouter(prefix="/sms", tags=["sms"], dependencies=[Depends(require_app_access("sms"))])

# Separate, un-gated router for Twilio webhooks (secured by signature validation instead).
webhook_router = APIRouter(prefix="/sms", tags=["sms-webhooks"])


def _security() -> SecurityService:
    try:
        return SecurityService()
    except Exception:  # pragma: no cover - defensive
        return None  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


@router.post("/{tenant_id}/numbers/{phone_number}/bind-sms")
async def bind_sms_number(
    tenant_id: str, phone_number: str, current_user: Dict = Depends(get_current_user_from_token)
):
    """Bind a tenant-owned number for SMS outbound + inbound webhook."""
    verify_tenant_access(current_user, tenant_id)
    try:
        result = await sms_number_service.bind_sms_number(tenant_id, phone_number)
        return {"message": "Number bound for SMS", "phone": result}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:  # pragma: no cover - defensive
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to bind number: {e}")


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------


@router.post("/{tenant_id}/leads", response_model=SmsLeadResponse, status_code=status.HTTP_201_CREATED)
async def create_lead(tenant_id: str, lead: SmsLeadCreate, current_user: Dict = Depends(get_current_user_from_token)):
    verify_tenant_access(current_user, tenant_id)
    try:
        return await sms_lead_service.import_lead(tenant_id, lead.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{tenant_id}/leads/bulk")
async def bulk_import_leads(
    tenant_id: str, payload: SmsLeadBulkImport, current_user: Dict = Depends(get_current_user_from_token)
):
    verify_tenant_access(current_user, tenant_id)
    return await sms_lead_service.bulk_import(tenant_id, [lead.model_dump() for lead in payload.leads])


@router.get("/{tenant_id}/leads", response_model=List[SmsLeadResponse])
async def list_leads(
    tenant_id: str, limit: int = 200, offset: int = 0, current_user: Dict = Depends(get_current_user_from_token)
):
    verify_tenant_access(current_user, tenant_id)
    return await sms_lead_service.list_leads(tenant_id, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------


@router.post("/{tenant_id}/campaigns", response_model=SmsCampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    tenant_id: str, payload: SmsCampaignCreate, current_user: Dict = Depends(get_current_user_from_token)
):
    verify_tenant_access(current_user, tenant_id)
    try:
        return await sms_campaign_service.create(tenant_id, payload.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{tenant_id}/campaigns/{campaign_id}", response_model=SmsCampaignResponse)
async def get_campaign(tenant_id: str, campaign_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    verify_tenant_access(current_user, tenant_id)
    campaign = await sms_campaign_service.update(campaign_id, {})  # returns current if no changes
    if not campaign or campaign.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
    return campaign


@router.put("/{tenant_id}/campaigns/{campaign_id}", response_model=SmsCampaignResponse)
async def update_campaign(
    tenant_id: str,
    campaign_id: str,
    payload: SmsCampaignUpdate,
    current_user: Dict = Depends(get_current_user_from_token),
):
    verify_tenant_access(current_user, tenant_id)
    existing = await sms_campaign_service.update(campaign_id, {})
    if not existing or existing.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
    return await sms_campaign_service.update(campaign_id, payload.model_dump(exclude_none=True))


@router.post("/{tenant_id}/campaigns/{campaign_id}/start")
async def start_campaign(tenant_id: str, campaign_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    verify_tenant_access(current_user, tenant_id)
    security = _security()
    if security:
        await security.enforce_rate_limit(tenant_id, limit=20, window_seconds=60, operation="sms_campaign_start")
    try:
        return await sms_campaign_service.start(tenant_id, campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{tenant_id}/campaigns/{campaign_id}/pause")
async def pause_campaign(tenant_id: str, campaign_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    verify_tenant_access(current_user, tenant_id)
    existing = await sms_campaign_service.update(campaign_id, {})
    if not existing or existing.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
    return await sms_campaign_service.pause(campaign_id)


# ---------------------------------------------------------------------------
# Inbox / conversations
# ---------------------------------------------------------------------------


@router.get("/{tenant_id}/conversations", response_model=List[SmsConversationResponse])
async def list_conversations(
    tenant_id: str, limit: int = 100, offset: int = 0, current_user: Dict = Depends(get_current_user_from_token)
):
    verify_tenant_access(current_user, tenant_id)
    return await sms_inbox_service.list_conversations(tenant_id, limit=limit, offset=offset)


@router.get("/{tenant_id}/conversations/{conversation_id}", response_model=SmsConversationDetail)
async def get_conversation(
    tenant_id: str, conversation_id: str, current_user: Dict = Depends(get_current_user_from_token)
):
    verify_tenant_access(current_user, tenant_id)
    detail = await sms_inbox_service.get_conversation_detail(tenant_id, conversation_id)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return detail


@router.post("/{tenant_id}/conversations/{conversation_id}/reply")
async def reply_to_conversation(
    tenant_id: str,
    conversation_id: str,
    payload: SmsSendNow,
    current_user: Dict = Depends(get_current_user_from_token),
):
    verify_tenant_access(current_user, tenant_id)
    security = _security()
    if security:
        await security.enforce_rate_limit(tenant_id, limit=120, window_seconds=60, operation="sms_send")
    try:
        return await sms_inbox_service.manual_reply(tenant_id, conversation_id, payload.body)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ---------------------------------------------------------------------------
# Suppressions
# ---------------------------------------------------------------------------


@router.get("/{tenant_id}/suppressions", response_model=List[SmsSuppressionResponse])
async def list_suppressions(
    tenant_id: str, limit: int = 500, offset: int = 0, current_user: Dict = Depends(get_current_user_from_token)
):
    verify_tenant_access(current_user, tenant_id)
    return await sms_suppression_service.list(tenant_id, limit=limit, offset=offset)


@router.post("/{tenant_id}/suppressions", response_model=SmsSuppressionResponse, status_code=status.HTTP_201_CREATED)
async def add_suppression(
    tenant_id: str, payload: SmsSuppressionCreate, current_user: Dict = Depends(get_current_user_from_token)
):
    verify_tenant_access(current_user, tenant_id)
    try:
        return await sms_suppression_service.add(tenant_id, payload.phone_number, payload.reason)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ---------------------------------------------------------------------------
# Twilio webhooks (signature-validated; not app-gated)
# ---------------------------------------------------------------------------


@webhook_router.post("/twilio/inbound")
async def twilio_inbound_sms(request: Request):
    """Inbound SMS from a lead. Validates signature, then records + handles reply/opt-out."""
    form = dict(await request.form())
    if not await validate_twilio_signature(request, form):
        logger.error("Invalid Twilio signature on inbound SMS — rejecting")
        return Response(status_code=403, content="Invalid signature")
    try:
        await sms_webhook_service.handle_inbound(form)
    except Exception as exc:  # pragma: no cover - never fail the webhook
        logger.error("Error handling inbound SMS: %s", exc, exc_info=True)
    # Empty TwiML response (no auto-reply).
    return Response(content="<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response/>", media_type="application/xml")


@webhook_router.post("/twilio/status")
async def twilio_sms_status(request: Request):
    """Outbound message status callback. Validates signature, updates message status."""
    form = dict(await request.form())
    if not await validate_twilio_signature(request, form):
        logger.error("Invalid Twilio signature on SMS status callback — rejecting")
        return Response(status_code=403, content="Invalid signature")
    try:
        await sms_webhook_service.handle_status(form)
    except Exception as exc:  # pragma: no cover - never fail the webhook
        logger.error("Error handling SMS status callback: %s", exc, exc_info=True)
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Inbox real-time WebSocket (live reply notifications)
# ---------------------------------------------------------------------------


@webhook_router.websocket("/{tenant_id}/inbox/ws")
async def inbox_websocket(websocket: WebSocket, tenant_id: str, token: str = ""):
    """Stream real-time inbox events (e.g. lead replies) for a tenant.

    Auth: an access token via the ``token`` query param (WebSockets can't use the
    HTTP app-access dependency). The user must have ``sms`` app access and the
    tenant must be in their accessible scope.
    """
    await websocket.accept()
    try:
        payload = auth_service.verify_token(token, token_type="access")
        if not payload or not payload.get("sub"):
            await websocket.close(code=4401)
            return
        user = await auth_service.get_user_by_id(str(payload["sub"]))
        if not user or not has_app_access(user, "sms"):
            await websocket.close(code=4403)
            return
        try:
            verify_tenant_access(user, tenant_id)
        except HTTPException:
            await websocket.close(code=4403)
            return
    except Exception:
        await websocket.close(code=4401)
        return

    client = await async_redis_client.get_client()
    pubsub = client.pubsub()
    await pubsub.subscribe(sms_inbox_channel(tenant_id))
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)
            if message and message.get("type") == "message":
                await websocket.send_text(message["data"])
            else:
                # Keepalive ping so idle connections aren't dropped by proxies.
                await websocket.send_text('{"type":"ping"}')
            await asyncio.sleep(0)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("SMS inbox websocket error: %s", exc)
    finally:
        try:
            await pubsub.unsubscribe(sms_inbox_channel(tenant_id))
            await pubsub.aclose()
        except Exception:
            pass
