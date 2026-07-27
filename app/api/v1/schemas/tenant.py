"""
Pydantic schemas for API request/response models.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, validator


# Base schemas
class BaseResponse(BaseModel):
    """Base response model."""

    success: bool = True
    message: str = "Operation completed successfully"


# Tenant schemas
class TenantCreate(BaseModel):
    """Schema for creating a new tenant."""

    name: str = Field(..., min_length=1, max_length=255, description="Tenant name")
    owner_email: EmailStr = Field(..., description="Business owner email")


class TenantUpdate(BaseModel):
    """Schema for updating tenant basic info."""

    name: str = Field(..., min_length=1, max_length=255)
    owner_email: EmailStr = Field(...)


class TenantResponse(BaseModel):
    """Schema for tenant response."""

    id: UUID
    name: str
    owner_email: EmailStr
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Business Config schemas (Tab 1)
class BusinessInfoCreate(BaseModel):
    """Schema for business info (Tab 1)."""

    business_name: str = Field(..., min_length=1, max_length=255)
    industry: str = Field(..., min_length=1, max_length=100)
    service_area: List[str] = Field(..., min_items=1, description="ZIP codes or cities")
    working_hours: Dict[str, Dict[str, str]] = Field(..., description="Per weekday hours")
    appointment_duration: int = Field(default=60, ge=15, le=480, description="Duration in minutes")
    buffer_time: int = Field(default=15, ge=0, le=60, description="Buffer time in minutes")
    same_day_rules: Optional[Dict[str, Any]] = Field(None, description="Same-day booking rules")
    urgency_rules: Optional[Dict[str, Any]] = Field(None, description="Emergency/urgency handling")
    contact_email: Optional[str] = Field(None, description="Business contact email")
    contact_phone: Optional[str] = Field(None, description="Business contact phone")
    address: Optional[str] = Field(None, description="Business address")
    website: Optional[str] = Field(None, description="Business website")
    description: Optional[str] = Field(None, description="Business description")

    @validator("working_hours")
    def validate_working_hours(cls, v):
        """Validate working hours format."""
        weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        for day in weekdays:
            if day in v:
                day_hours = v[day]
                if "start" not in day_hours or "end" not in day_hours:
                    raise ValueError(f"Day {day} must have 'start' and 'end' times")
        return v


class BusinessInfoUpdate(BaseModel):
    """Schema for updating business info."""

    business_name: Optional[str] = Field(None, min_length=1, max_length=255)
    industry: Optional[str] = Field(None, min_length=1, max_length=100)
    service_area: Optional[List[str]] = Field(None, min_items=1)
    working_hours: Optional[Dict[str, Dict[str, str]]] = Field(None)
    appointment_duration: Optional[int] = Field(None, ge=15, le=480)
    buffer_time: Optional[int] = Field(None, ge=0, le=60)
    same_day_rules: Optional[Dict[str, Any]] = Field(None)
    urgency_rules: Optional[Dict[str, Any]] = Field(None)


# AI Agent Settings schemas (Tab 2)
class AgentSettingsCreate(BaseModel):
    """Schema for AI agent settings (Tab 2)."""

    greeting_script: Optional[str] = Field(None, max_length=1000)
    recording_disclosure: Optional[str] = Field(None, max_length=1000)
    escalation_phone: Optional[str] = Field(None, pattern=r"^\+?[1-9]\d{1,14}$")


class AgentSettingsUpdate(BaseModel):
    """Schema for updating AI agent settings."""

    greeting_script: Optional[str] = Field(None, max_length=1000)
    recording_disclosure: Optional[str] = Field(None, max_length=1000)
    escalation_phone: Optional[str] = Field(None, pattern=r"^\+?[1-9]\d{1,14}$")


# Twilio Integration schemas (Tab 3)
class TwilioIntegrationCreate(BaseModel):
    """Schema for Twilio integration (Tab 3)."""

    twilio_account_sid: Optional[str] = Field(None, min_length=34, max_length=34)
    twilio_auth_token: Optional[str] = Field(None, min_length=32, max_length=32)
    twilio_phone_number: Optional[str] = Field(None, pattern=r"^\+?[1-9]\d{1,14}$")


class TwilioIntegrationUpdate(BaseModel):
    """Schema for updating Twilio integration."""

    twilio_account_sid: Optional[str] = Field(None, min_length=34, max_length=34)
    twilio_auth_token: Optional[str] = Field(None, min_length=32, max_length=32)
    twilio_phone_number: Optional[str] = Field(None, pattern=r"^\+?[1-9]\d{1,14}$")


# Combined tenant configuration schemas
class TenantConfigCreate(BaseModel):
    """Schema for creating complete tenant configuration."""

    tenant: TenantCreate
    business_info: BusinessInfoCreate
    agent_settings: AgentSettingsCreate
    twilio_integration: TwilioIntegrationCreate


class TenantConfigUpdate(BaseModel):
    """Schema for updating tenant configuration."""

    tenant: Optional[TenantUpdate] = None
    business_info: Optional[BusinessInfoUpdate] = None
    agent_settings: Optional[AgentSettingsUpdate] = None
    twilio_integration: Optional[TwilioIntegrationUpdate] = None


# Service type schemas
class ServiceTypeCreate(BaseModel):
    """Schema for service type selection."""

    service_type: str = Field(..., description="Service type from dropdown")

    @validator("service_type")
    def validate_service_type(cls, v):
        """Validate service type."""
        from app.core.validators import validate_service_type

        return validate_service_type(v)


# Agent Policy schemas
class AgentPolicyCreate(BaseModel):
    """Schema for creating agent policy."""

    service_type: str = Field(..., description="Service type")
    prompt_template: str = Field(..., min_length=100, description="Prompt template")
    forbidden_topics: Optional[List[str]] = Field(None, description="Topics to avoid")
    confirmation_format: Dict[str, Any] = Field(..., description="Confirmation schema")
    escalation_rules: Optional[Dict[str, Any]] = Field(None, description="Escalation rules")


class AgentPolicyResponse(BaseModel):
    """Schema for agent policy response."""

    id: UUID
    tenant_id: UUID
    service_type: str
    prompt_template: str
    forbidden_topics: Optional[List[str]]
    confirmation_format: Dict[str, Any]
    escalation_rules: Optional[Dict[str, Any]]
    version: int
    is_applied: bool
    is_draft: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Business Config response schemas
class BusinessConfigResponse(BaseModel):
    """Schema for business config response."""

    id: UUID
    tenant_id: UUID
    business_name: str
    industry: str
    service_area: List[str]
    working_hours: Dict[str, Dict[str, str]]
    appointment_duration: int
    buffer_time: int
    same_day_rules: Optional[Dict[str, Any]]
    urgency_rules: Optional[Dict[str, Any]]
    greeting_script: Optional[str]
    recording_disclosure: Optional[str]
    escalation_phone: Optional[str]
    twilio_account_sid: Optional[str]
    twilio_auth_token: Optional[str]  # Note: This should be masked in production
    twilio_phone_number: Optional[str]
    version: int
    is_applied: bool
    is_draft: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Provisioning schemas
class ProvisioningJobResponse(BaseModel):
    """Schema for provisioning job response."""

    id: UUID
    tenant_id: UUID
    job_type: str
    status: str
    steps: Dict[str, Any]
    error_message: Optional[str]
    retry_count: int
    max_retries: int
    idempotency_key: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Phone number schemas
class PhoneNumberResponse(BaseModel):
    """Schema for phone number response."""

    id: UUID
    tenant_id: UUID
    twilio_sid: str
    e164_number: str
    livekit_sip_uri: Optional[str]
    livekit_room_template: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Error schemas
class ErrorResponse(BaseModel):
    """Schema for error responses."""

    success: bool = False
    error: str
    details: Optional[Dict[str, Any]] = None
