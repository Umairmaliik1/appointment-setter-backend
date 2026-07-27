"""
Contact/Lead submission schemas for API requests and responses.
"""

from typing import Optional
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, EmailStr, field_validator, model_validator


class ContactCreate(BaseModel):
    """Schema for creating a new contact/lead submission."""

    email: Optional[EmailStr] = Field(None, description="Contact email address")
    phone_number: Optional[str] = Field(None, description="Contact phone number")

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, v):
        """Validate phone number format if provided."""
        if v is not None:
            # Remove common formatting characters
            cleaned = v.replace("-", "").replace(" ", "").replace("(", "").replace(")", "").replace("+", "")
            # Check if it's a valid phone number (digits only, reasonable length)
            if not cleaned.isdigit():
                raise ValueError("Phone number must contain only digits and common formatting characters")
            if len(cleaned) < 10 or len(cleaned) > 15:
                raise ValueError("Phone number must be between 10 and 15 digits")
        return v

    @model_validator(mode="after")
    def validate_at_least_one(self):
        """Ensure at least one of email or phone_number is provided."""
        if not self.email and not self.phone_number:
            raise ValueError("At least one of 'email' or 'phone_number' must be provided")
        return self


class ContactResponse(BaseModel):
    """Schema for contact submission response."""

    id: UUID
    email: Optional[str]
    phone_number: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True
