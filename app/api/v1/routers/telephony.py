"""
Telephony ownership API routes.
"""

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.routers.auth import get_current_user_from_token, require_app_access, verify_tenant_access
from app.api.v1.schemas.telephony import (
    BindColdCallerOutboundRequest,
    TelephonyBindResponse,
    TelephonyPhoneNumberResponse,
    TelephonyStatusResponse,
    UnbindColdCallerOutboundRequest,
)
from app.api.v1.services.telephony import telephony_service

router = APIRouter(prefix="/telephony", tags=["telephony"], dependencies=[Depends(require_app_access("appointment_setter"))])


@router.get("/tenant/{tenant_id}/status", response_model=TelephonyStatusResponse)
async def get_tenant_telephony_status(tenant_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    verify_tenant_access(current_user, tenant_id)
    try:
        status_payload = await telephony_service.get_tenant_status(tenant_id)
        return TelephonyStatusResponse(**status_payload)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get telephony status: {exc}")


@router.get("/tenant/{tenant_id}/numbers", response_model=List[TelephonyPhoneNumberResponse])
async def list_tenant_telephony_numbers(tenant_id: str, current_user: Dict = Depends(get_current_user_from_token)):
    verify_tenant_access(current_user, tenant_id)
    try:
        numbers = await telephony_service.list_tenant_numbers(tenant_id)
        return [TelephonyPhoneNumberResponse(**row) for row in numbers]
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to list telephony numbers: {exc}"
        )


@router.post("/tenant/{tenant_id}/cold-caller-outbound/bind", response_model=TelephonyBindResponse)
async def bind_cold_caller_outbound_number(
    tenant_id: str,
    payload: BindColdCallerOutboundRequest,
    current_user: Dict = Depends(get_current_user_from_token),
):
    verify_tenant_access(current_user, tenant_id)
    try:
        updated = await telephony_service.bind_cold_caller_outbound(tenant_id=tenant_id, phone_number=payload.phone_number)
        return TelephonyBindResponse(
            message="Cold Caller outbound number bound successfully",
            phone_number=TelephonyPhoneNumberResponse(**updated),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to bind number: {exc}")


@router.post("/tenant/{tenant_id}/cold-caller-outbound/unbind", response_model=TelephonyBindResponse)
async def unbind_cold_caller_outbound_number(
    tenant_id: str,
    payload: UnbindColdCallerOutboundRequest,
    current_user: Dict = Depends(get_current_user_from_token),
):
    verify_tenant_access(current_user, tenant_id)
    try:
        updated = await telephony_service.unbind_cold_caller_outbound(tenant_id=tenant_id, phone_number=payload.phone_number)
        return TelephonyBindResponse(
            message="Cold Caller outbound number unbound successfully",
            phone_number=TelephonyPhoneNumberResponse(**updated),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to unbind number: {exc}")
