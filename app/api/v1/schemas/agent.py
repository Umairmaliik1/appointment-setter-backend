"""
Agent schemas for API requests and responses.
"""

from typing import Optional

from pydantic import BaseModel, Field, validator

from app.core.validators import validate_service_type


class AgentCreate(BaseModel):
    """Schema for creating a new agent."""

    name: str = Field(..., min_length=1, max_length=100, description="Agent name (e.g., 'Receptionist Sarah')")
    voice_id: str = Field(..., description="Gemini Live voice ID")
    language: str = Field(default="en-US", description="Language code (e.g., en-US, es-ES)")
    greeting_message: str = Field(..., min_length=10, max_length=1000, description="Agent greeting message")
    service_type: str = Field(..., description="Service type")
    system_prompt: Optional[str] = Field(
        None,
        min_length=1,
        max_length=4000,
        description="Custom system prompt for the agent. Falls back to service-type template when omitted.",
    )

    @validator("service_type")
    def validate_service_type(cls, v):
        """Validate service type."""
        return validate_service_type(v)


class AgentUpdate(BaseModel):
    """Schema for updating an agent."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    voice_id: Optional[str] = None
    language: Optional[str] = None
    greeting_message: Optional[str] = Field(None, min_length=10, max_length=1000)
    service_type: Optional[str] = None
    system_prompt: Optional[str] = Field(None, min_length=1, max_length=4000)
    status: Optional[str] = None

    @validator("service_type")
    def validate_service_type(cls, v):
        """Validate service type."""
        if v is None:
            return v
        return validate_service_type(v)


class AgentResponse(BaseModel):
    """Schema for agent response."""

    id: str
    agent_type: str = "voice"
    tenant_id: str
    name: str
    voice_id: str
    language: str
    greeting_message: str
    service_type: str
    system_prompt: str = ""
    status: str
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class VoiceOption(BaseModel):
    """Schema for a Gemini Live voice option."""

    voice_id: str
    name: str
    description: str
    category: str  # male, female, neutral
    use_case: str  # conversational, narration, customer_service, general


class VoiceListResponse(BaseModel):
    """Schema for list of available voices."""

    voices: list[VoiceOption]
    total: int


class PromptTemplateOption(BaseModel):
    """Starter prompt template for agent configuration UI."""

    id: str
    label: str
    text: str


class PromptTemplateListResponse(BaseModel):
    """List of starter prompt templates."""

    templates: list[PromptTemplateOption]


class PromptPreviewResponse(BaseModel):
    """Default system prompt preview for a service type."""

    prompt: str
    service_type: str
    agent_name: str
