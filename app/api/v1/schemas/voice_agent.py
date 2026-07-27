"""
Pydantic schemas for voice/Twilio integration operations.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class TwilioIntegrationConfig(BaseModel):
    """Schema for Twilio integration configuration."""

    account_sid: str = Field(..., description="Twilio Account SID")
    auth_token: str = Field(..., description="Twilio Auth Token")
    phone_number: Optional[str] = Field(None, description="Twilio phone number (optional for initial setup)")
    webhook_url: Optional[str] = Field(
        None, description="Webhook URL (optional, uses TWILIO_WEBHOOK_BASE_URL if not provided)"
    )
    status_callback_url: Optional[str] = Field(
        None, description="Status callback URL (optional, uses TWILIO_WEBHOOK_BASE_URL if not provided)"
    )
    verify_credentials: bool = Field(default=True, description="Verify credentials")


class TwilioCredentialTest(BaseModel):
    """Schema for testing Twilio credentials."""

    account_sid: str = Field(..., description="Twilio Account SID")
    auth_token: str = Field(..., description="Twilio Auth Token")
    phone_number: Optional[str] = Field(None, description="Phone number to test (optional for basic credential verification)")


class TwilioCredentialTestResponse(BaseModel):
    """Schema for Twilio credential test response."""

    success: bool = Field(..., description="Test success status")
    message: str = Field(..., description="Test result message")
    phone_number: Optional[str] = Field(None, description="Verified phone number")
    account_info: Optional[Dict[str, Any]] = Field(None, description="Account information")
    is_test_account: bool = Field(default=False, description="Whether these are test account credentials")
