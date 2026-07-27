"""
Organization (white-label hierarchy) schemas.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field

OrgType = Literal["platform", "partner", "customer"]
OrgStatus = Literal["active", "inactive", "suspended"]
OnboardingStatus = Literal["invited", "active", "suspended"]


class OrgBranding(BaseModel):
    brand_name: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    support_email: Optional[str] = None


class OrgCreateRequest(BaseModel):
    org_type: OrgType
    name: str = Field(..., min_length=1, max_length=255)
    parent_org_id: Optional[str] = None
    status: OrgStatus = "active"
    branding: Optional[OrgBranding] = None
    legacy_tenant_id: Optional[str] = None


class OrgUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    status: Optional[OrgStatus] = None
    branding: Optional[OrgBranding] = None


class OrgResponse(BaseModel):
    id: str
    org_type: OrgType
    parent_org_id: Optional[str]
    name: str
    status: OrgStatus
    branding: Dict[str, Any] = Field(default_factory=dict)
    legacy_tenant_id: Optional[str] = None
    appointment_setter_enabled: Optional[bool] = None
    seat_limit: Optional[int] = None
    seat_usage: Optional[int] = None
    seat_remaining: Optional[int] = None
    over_seat_limit: Optional[bool] = None
    onboarding_status: Optional[OnboardingStatus] = None
    created_at: datetime
    updated_at: datetime


class OrgMembershipCreateRequest(BaseModel):
    user_id: str
    role: str = Field(..., min_length=3, max_length=64)
    status: Literal["active", "inactive"] = "active"


class OrgMemberInviteRequest(BaseModel):
    email: str
    username: str = Field(..., min_length=3, max_length=72)
    first_name: str = Field(..., min_length=1, max_length=72)
    last_name: str = Field(..., min_length=1, max_length=72)
    role: str = Field(..., min_length=3, max_length=64)
    password: Optional[str] = Field(None, min_length=8, max_length=72)
    status: Literal["active", "inactive"] = "active"
    send_invite_email: bool = True
    invite_base_url: Optional[str] = None


class OrgMembershipResponse(BaseModel):
    id: str
    org_id: str
    user_id: str
    role: str
    status: Literal["active", "inactive"]
    created_at: datetime
    updated_at: datetime


class PartnerEntitlementsUpdateRequest(BaseModel):
    appointment_setter_enabled: bool
    approved_by_user_id: Optional[str] = None
    approval_notes: Optional[str] = None
    seat_limit: Optional[int] = Field(default=None, ge=1, le=100000)
    onboarding_status: Optional[OnboardingStatus] = None


class PartnerEntitlementsResponse(BaseModel):
    partner_org_id: str
    appointment_setter_enabled: bool
    seat_limit: int = 25
    seat_usage: int = 0
    seat_remaining: int = 25
    over_seat_limit: bool = False
    onboarding_status: OnboardingStatus = "invited"
    approved_by_user_id: Optional[str] = None
    approval_notes: Optional[str] = None
    updated_at: datetime


class PartnerBrandingUpdateRequest(BaseModel):
    branding: OrgBranding


class PartnerBrandingResponse(BaseModel):
    partner_org_id: str
    branding: Dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class PartnerOwnerCreateRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=72)
    first_name: str = Field(..., min_length=1, max_length=72)
    last_name: str = Field(..., min_length=1, max_length=72)
    password: Optional[str] = Field(None, min_length=8, max_length=72)
    role: Literal["partner_owner", "partner_admin"] = "partner_owner"
    allowed_app_ids: List[str] = Field(default_factory=lambda: ["appointment_setter", "users"])
    default_app_id: Optional[str] = "appointment_setter"


class PartnerWithOwnerCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    status: OrgStatus = "active"
    branding: Optional[OrgBranding] = None
    legacy_tenant_id: Optional[str] = None
    owner: PartnerOwnerCreateRequest
    seat_limit: int = Field(default=25, ge=1, le=100000)
    approval_notes: Optional[str] = None
    send_invite_email: bool = True
    invite_base_url: Optional[str] = None


class PartnerOwnerUserResponse(BaseModel):
    id: str
    email: str
    username: str
    first_name: str
    last_name: str
    role: str
    active_org_id: Optional[str] = None
    allowed_app_ids: List[str] = Field(default_factory=list)
    default_app_id: Optional[str] = None


class PartnerWithOwnerResponse(BaseModel):
    partner_org: OrgResponse
    owner_user: PartnerOwnerUserResponse
    membership: OrgMembershipResponse
    entitlements: PartnerEntitlementsResponse
    invite_email_sent: bool = False


class OrgMemberResendInviteRequest(BaseModel):
    invite_base_url: Optional[str] = None


class OrgMemberInviteDispatchResponse(BaseModel):
    org_id: str
    user_id: str
    invite_email_sent: bool
    message: str


class OrgLifecycleResponse(BaseModel):
    org_id: str
    status: OrgStatus
    onboarding_status: Optional[OnboardingStatus] = None
    updated_at: datetime
