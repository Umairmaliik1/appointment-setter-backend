"""
Telephony ownership schemas.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


UsageRole = Literal["voice_agent_inbound", "cold_caller_outbound"]


class TelephonyPhoneNumberResponse(BaseModel):
    id: str
    tenant_id: str
    phone_number: str
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    twilio_integration_id: str
    status: str
    usage_role: UsageRole
    role_status: str
    conflict_code: Optional[str] = None
    conflict_message: Optional[str] = None
    created_at: str
    updated_at: str


class TelephonyStatusResponse(BaseModel):
    tenant_id: str
    has_shared_twilio_credentials: bool
    shared_account_sid: Optional[str] = None
    total_numbers: int = 0
    voice_agent_inbound_numbers: int = 0
    cold_caller_outbound_numbers: int = 0
    conflicts_count: int = 0
    conflicts: List[TelephonyPhoneNumberResponse] = Field(default_factory=list)


class BindColdCallerOutboundRequest(BaseModel):
    phone_number: str = Field(..., min_length=3, max_length=32)


class UnbindColdCallerOutboundRequest(BaseModel):
    phone_number: str = Field(..., min_length=3, max_length=32)


class TelephonyBindResponse(BaseModel):
    message: str
    phone_number: TelephonyPhoneNumberResponse
