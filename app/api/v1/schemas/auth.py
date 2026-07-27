"""
Authentication schemas for API requests and responses.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, validator

from app.core.validators import validate_password
from app.models.auth import UserRole, UserStatus


# Base schemas
class TokenResponse(BaseModel):
    """Token response schema."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserResponse"


# User schemas
class UserCreate(BaseModel):
    """Schema for creating a new user."""

    email: EmailStr
    username: str = Field(..., min_length=3, max_length=72)
    password: str = Field(..., min_length=8, max_length=72)
    first_name: str = Field(..., min_length=1, max_length=72)
    last_name: str = Field(..., min_length=1, max_length=72)
    role: UserRole = Field(default=UserRole.TENANT_USER)
    tenant_id: Optional[UUID] = Field(None, description="Required for tenant users")

    @validator("password")
    def validate_password(cls, v):
        """Validate password strength."""
        return validate_password(v)


class UserUpdate(BaseModel):
    """Schema for updating user information."""

    email: Optional[EmailStr] = None
    username: Optional[str] = Field(None, min_length=3, max_length=72)
    first_name: Optional[str] = Field(None, min_length=1, max_length=72)
    last_name: Optional[str] = Field(None, min_length=1, max_length=72)
    role: Optional[UserRole] = None
    status: Optional[UserStatus] = None
    tenant_id: Optional[UUID] = None


class UserResponse(BaseModel):
    """Schema for user response."""

    id: UUID
    email: str
    username: str
    first_name: str
    last_name: str
    role: UserRole
    status: UserStatus
    tenant_id: Optional[UUID]
    platform_role_id: Optional[str] = None
    allowed_app_ids: List[str] = Field(default_factory=list)
    default_app_id: Optional[str] = None
    org_memberships: List[Dict[str, Any]] = Field(default_factory=list)
    active_org_id: Optional[str] = None
    is_email_verified: bool
    last_login: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserListResponse(BaseModel):
    """Schema for user list response."""

    users: List[UserResponse]
    total: int
    page: int
    size: int


# Authentication schemas
class UserLogin(BaseModel):
    """Schema for user login request."""

    email: EmailStr
    password: str
    remember_me: bool = Field(default=False, description="Remember login for longer session")


class LoginRequest(BaseModel):
    """Schema for login request."""

    email: EmailStr
    password: str
    remember_me: bool = Field(default=False, description="Remember login for longer session")


class PasswordChange(BaseModel):
    """Schema for password change request."""

    current_password: str
    new_password: str = Field(..., min_length=8, max_length=72)


class PasswordChangeRequest(BaseModel):
    """Schema for password change request."""

    current_password: str
    new_password: str = Field(..., min_length=8, max_length=72)

    @validator("new_password")
    def validate_new_password(cls, v):
        """Validate new password strength."""
        return validate_password(v, field_name="New password")


class PasswordResetRequest(BaseModel):
    """Schema for password reset request."""

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Schema for password reset confirmation."""

    token: str
    new_password: str = Field(..., min_length=8, max_length=72)

    @validator("new_password")
    def validate_new_password(cls, v):
        """Validate new password strength."""
        return validate_password(v, field_name="New password")


class EmailVerificationRequest(BaseModel):
    """Schema for email verification request."""

    token: str


class RefreshTokenRequest(BaseModel):
    """Schema for refresh token request."""

    refresh_token: Optional[str] = None


# Permission schemas
class PermissionResponse(BaseModel):
    """Schema for permission response."""

    id: UUID
    name: str
    description: Optional[str]
    resource: str
    action: str
    created_at: datetime

    class Config:
        from_attributes = True


class UserPermissionResponse(BaseModel):
    """Schema for user permission response."""

    id: UUID
    user_id: UUID
    permission: PermissionResponse
    tenant_id: Optional[UUID]
    granted_at: datetime
    expires_at: Optional[datetime]
    is_active: bool

    class Config:
        from_attributes = True


class PermissionGrantRequest(BaseModel):
    """Schema for granting permission to user."""

    user_id: UUID
    permission_id: UUID
    tenant_id: Optional[UUID] = None
    expires_at: Optional[datetime] = None


class PermissionRevokeRequest(BaseModel):
    """Schema for revoking permission from user."""

    user_id: UUID
    permission_id: UUID
    tenant_id: Optional[UUID] = None


# Session schemas
class UserSessionResponse(BaseModel):
    """Schema for user session response."""

    id: UUID
    user_agent: Optional[str]
    ip_address: Optional[str]
    is_active: bool
    created_at: datetime
    last_activity: datetime
    access_token_expires: datetime

    class Config:
        from_attributes = True


class SessionListResponse(BaseModel):
    """Schema for session list response."""

    sessions: List[UserSessionResponse]
    total: int
    page: int
    size: int


# Error schemas
class AuthErrorResponse(BaseModel):
    """Schema for authentication error responses."""

    error: str
    error_description: Optional[str] = None
    error_code: Optional[str] = None


# Additional missing schemas
class ForgotPasswordRequest(BaseModel):
    """Schema for forgot password request."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Schema for reset password request."""

    token: str
    new_password: str = Field(..., min_length=8, max_length=72)

    @validator("new_password")
    def validate_new_password(cls, v):
        """Validate new password strength."""
        return validate_password(v, field_name="New password")


class SetupPasswordConfirmRequest(BaseModel):
    """Schema for first-time setup password confirmation."""

    token: str
    new_password: str = Field(..., min_length=8, max_length=72)

    @validator("new_password")
    def validate_new_password(cls, v):
        """Validate new password strength."""
        return validate_password(v, field_name="New password")


class PermissionCreate(BaseModel):
    """Schema for creating a permission."""

    name: str
    description: Optional[str] = None
    resource: str
    action: str


class LogoutRequest(BaseModel):
    """Schema for logout request."""

    refresh_token: Optional[str] = None


class RoleCreate(BaseModel):
    """Schema for creating a role."""

    name: str
    description: Optional[str] = None
    permissions: List[UUID] = []


# Update TokenResponse to include user
TokenResponse.model_rebuild()
