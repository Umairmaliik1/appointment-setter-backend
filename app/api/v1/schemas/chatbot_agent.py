"""
Schemas for multi-domain chatbot agent configuration, live chat, and launcher embedding.
"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, root_validator, validator

DOMAIN_KEYS = (
    "healthcare",
    "real_estate",
    "ecommerce",
    "customer_support",
    "education",
    "home_services",
    "professional_services",
    "custom",
)
TONE_VALUES = ("professional", "friendly", "sales", "empathetic", "technical")
RESPONSE_STYLE_VALUES = ("concise", "balanced", "detailed")
LAUNCHER_POSITION_VALUES = ("bottom-right", "bottom-left")


def _validate_hex_color(value: str) -> str:
    color = value.strip()
    if len(color) != 7 or not color.startswith("#"):
        raise ValueError("Color must be in hex format like #1A2B3C")
    hex_part = color[1:]
    if not all(char in "0123456789abcdefABCDEF" for char in hex_part):
        raise ValueError("Color must be a valid hex value")
    return color


class ChatbotTheme(BaseModel):
    """Theme settings for chatbot panel UI."""

    primary_color: str = Field(..., description="Primary accent color in hex format")
    background_color: str = Field(..., description="Background color in hex format")
    text_color: str = Field(..., description="Text color in hex format")

    @validator("primary_color", "background_color", "text_color")
    def validate_hex_color(cls, value: str) -> str:
        return _validate_hex_color(value)


class ChatbotBehaviorConfig(BaseModel):
    """Structured behavior controls for chatbot runtime."""

    persona: str = Field(..., min_length=1, max_length=120)
    goal: str = Field(..., min_length=1, max_length=500)
    tone: Literal[
        "professional",
        "friendly",
        "sales",
        "empathetic",
        "technical",
    ]
    response_style: Literal["concise", "balanced", "detailed"]
    allowed_topics: List[str] = Field(default_factory=list, max_items=50)
    blocked_topics: List[str] = Field(default_factory=list, max_items=50)
    escalation_instructions: str = Field(..., min_length=1, max_length=500)
    custom_instructions: str = Field(default="", max_length=2000)
    language: str = Field(default="en", min_length=2, max_length=20)

    @validator("allowed_topics", "blocked_topics")
    def normalize_topic_lists(cls, value: List[str]) -> List[str]:
        cleaned = [entry.strip() for entry in value if entry and entry.strip()]
        return cleaned

    @validator("persona", "goal", "escalation_instructions", "custom_instructions", "language")
    def trim_text_fields(cls, value: str) -> str:
        return value.strip()


class ChatbotFaqItem(BaseModel):
    """FAQ knowledge item."""

    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1, max_length=2000)

    @validator("question", "answer")
    def trim_faq_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("FAQ fields cannot be empty")
        return cleaned


class ChatbotKnowledgeConfig(BaseModel):
    """Manual knowledge controls for chatbot context."""

    business_facts: str = Field(..., min_length=1, max_length=8000)
    faq_items: List[ChatbotFaqItem] = Field(default_factory=list, max_items=100)

    @validator("business_facts")
    def validate_business_facts(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("business_facts cannot be empty")
        return cleaned


class ChatbotLauncherConfig(BaseModel):
    """Floating launcher setup."""

    mode: Literal["launcher"] = "launcher"
    position: Literal["bottom-right", "bottom-left"] = "bottom-right"
    button_label: str = Field(..., min_length=1, max_length=50)
    button_icon: str = Field(default="message-circle", min_length=1, max_length=40)
    accent_color: str = Field(..., description="Hex color for launcher button")

    @validator("accent_color")
    def validate_accent_color(cls, value: str) -> str:
        return _validate_hex_color(value)

    @validator("button_label", "button_icon")
    def trim_launcher_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Launcher text fields cannot be empty")
        return cleaned


class ChatbotAgentCreate(BaseModel):
    """Create request for multi-domain chatbot agent."""

    name: str = Field(..., min_length=1, max_length=100)
    welcome_message: str = Field(..., min_length=1, max_length=1000)
    allowed_origins: List[str] = Field(..., min_items=1, max_items=100)
    theme: ChatbotTheme
    status: Literal["active", "inactive"] = "active"
    domain_key: Literal[
        "healthcare",
        "real_estate",
        "ecommerce",
        "customer_support",
        "education",
        "home_services",
        "professional_services",
        "custom",
    ]
    custom_domain_name: Optional[str] = Field(None, min_length=1, max_length=80)
    behavior_config: ChatbotBehaviorConfig
    knowledge_config: ChatbotKnowledgeConfig
    launcher_config: ChatbotLauncherConfig

    @validator("name", "welcome_message")
    def trim_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty")
        return cleaned

    @validator("allowed_origins")
    def validate_allowed_origins(cls, value: List[str]) -> List[str]:
        cleaned: List[str] = []
        for origin in value:
            normalized = origin.strip().rstrip("/")
            if not (normalized.startswith("http://") or normalized.startswith("https://")):
                raise ValueError("Each allowed origin must start with http:// or https://")
            cleaned.append(normalized)
        return cleaned

    @root_validator(skip_on_failure=True)
    def validate_custom_domain_fields(cls, values):
        domain_key = values.get("domain_key")
        custom_name = values.get("custom_domain_name")
        if domain_key == "custom" and not custom_name:
            raise ValueError("custom_domain_name is required when domain_key is 'custom'")
        if domain_key != "custom":
            values["custom_domain_name"] = None
        return values


class ChatbotAgentUpdate(BaseModel):
    """Update request for multi-domain chatbot agent."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    welcome_message: Optional[str] = Field(None, min_length=1, max_length=1000)
    allowed_origins: Optional[List[str]] = Field(None, min_items=1, max_items=100)
    theme: Optional[ChatbotTheme] = None
    status: Optional[Literal["active", "inactive"]] = None
    domain_key: Optional[Literal[
        "healthcare",
        "real_estate",
        "ecommerce",
        "customer_support",
        "education",
        "home_services",
        "professional_services",
        "custom",
    ]] = None
    custom_domain_name: Optional[str] = Field(None, min_length=1, max_length=80)
    behavior_config: Optional[ChatbotBehaviorConfig] = None
    knowledge_config: Optional[ChatbotKnowledgeConfig] = None
    launcher_config: Optional[ChatbotLauncherConfig] = None

    @validator("name", "welcome_message", "custom_domain_name")
    def trim_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty")
        return cleaned

    @validator("allowed_origins")
    def validate_allowed_origins(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return value

        cleaned: List[str] = []
        for origin in value:
            normalized = origin.strip().rstrip("/")
            if not (normalized.startswith("http://") or normalized.startswith("https://")):
                raise ValueError("Each allowed origin must start with http:// or https://")
            cleaned.append(normalized)
        return cleaned


class ChatbotAgentResponse(BaseModel):
    """Response model for chatbot agent."""

    id: str
    agent_type: str = "chatbot"
    owner_user_id: str
    name: str
    welcome_message: str
    allowed_origins: List[str]
    theme: ChatbotTheme
    status: str
    domain_key: str
    custom_domain_name: Optional[str]
    behavior_config: ChatbotBehaviorConfig
    knowledge_config: ChatbotKnowledgeConfig
    launcher_config: ChatbotLauncherConfig
    embed_token_version: int
    created_at: str
    updated_at: str


class ChatbotEmbedTokenRequest(BaseModel):
    """Request model for generating launcher embed tokens."""

    origin: str = Field(..., description="Origin requesting embed token")
    never_expires: bool = Field(
        False,
        description="When true, generate a non-expiring embed token.",
    )
    expires_in_minutes: Optional[int] = Field(
        None,
        ge=5,
        le=10080,
        description="Optional token lifetime in minutes (5 minutes to 7 days).",
    )

    @validator("origin")
    def validate_origin(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not (normalized.startswith("http://") or normalized.startswith("https://")):
            raise ValueError("Origin must start with http:// or https://")
        return normalized

    @root_validator(skip_on_failure=True)
    def validate_expiry_options(cls, values):
        never_expires = bool(values.get("never_expires", False))
        expires_in_minutes = values.get("expires_in_minutes")
        if never_expires and expires_in_minutes is not None:
            raise ValueError("expires_in_minutes cannot be provided when never_expires is true")
        return values


class ChatbotEmbedTokenResponse(BaseModel):
    """Response model for launcher token generation."""

    token: str
    expires_at: Optional[str] = None
    never_expires: bool = False
    token_version: int
    loader_url: str
    launcher_script: str


class ChatbotEmbedConfigResponse(BaseModel):
    """Public config payload returned to chat panel runtime."""

    chatbot_id: str
    name: str
    welcome_message: str
    theme: ChatbotTheme
    status: str
    generated_at: datetime


class ChatbotChatHistoryMessage(BaseModel):
    """Single chat history message for chatbot runtime."""

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=8000)

    @validator("content")
    def validate_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Content cannot be empty")
        return cleaned


class ChatbotEmbedStreamRequest(BaseModel):
    """Request payload for chatbot streaming response."""

    message: str = Field(..., min_length=1, max_length=4000)
    history: List[ChatbotChatHistoryMessage] = Field(default_factory=list)

    @validator("message")
    def validate_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Message cannot be empty")
        return cleaned


class ChatbotEmbedRevokeResponse(BaseModel):
    """Response model for token revocation endpoint."""

    chatbot_id: str
    embed_token_version: int
    revoked_at: datetime


class ChatbotRuntimeLogResponse(BaseModel):
    """Runtime log response model."""

    request_id: str
    chatbot_id: str
    status: str
    latency_ms: int
    error_code: Optional[str] = None
    timestamp: datetime


class ChatbotRuntimeControlRequest(BaseModel):
    """Admin request model for runtime kill switch."""

    enabled: bool


class ChatbotRuntimeControlResponse(BaseModel):
    """Admin response model for runtime kill switch."""

    enabled: bool
    updated_at: datetime
    updated_by: str


class CreateEmbedSessionRequest(BaseModel):
    visitor_session_id: str = Field(..., min_length=1, max_length=200)
    page_url: Optional[str] = Field(None, max_length=2000)
    page_title: Optional[str] = Field(None, max_length=300)

    @validator("visitor_session_id", "page_url", "page_title", pre=True)
    def trim_optional_fields(cls, value):
        if value is None:
            return value
        return str(value).strip()


class VisitorMessageRequest(BaseModel):
    visitor_session_id: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)

    @validator("visitor_session_id", "message")
    def trim_visitor_message_fields(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty")
        return cleaned


class OperatorMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)

    @validator("content")
    def trim_operator_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Content cannot be empty")
        return cleaned


class ChatMessageResponse(BaseModel):
    id: str
    session_id: str
    chatbot_id: str
    sender_type: Literal["visitor", "bot", "human", "system"]
    sender_id: Optional[str] = None
    content: str
    created_at: datetime


class ChatSessionResponse(BaseModel):
    id: str
    chatbot_id: str
    owner_user_id: str
    origin: str
    page_url: str
    page_title: str = ""
    visitor_session_id: str
    visitor_label: str
    is_returning_visitor: bool = False
    status: Literal["open", "closed"]
    control_mode: Literal["bot", "human"]
    assigned_operator_id: Optional[str] = None
    assigned_operator_name: Optional[str] = None
    started_at: datetime
    last_activity_at: datetime
    last_restored_at: Optional[datetime] = None
    taken_over_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    last_message_preview: Optional[str] = None


class CreateEmbedSessionResponse(BaseModel):
    session: ChatSessionResponse
    messages: List[ChatMessageResponse]
    session_token: str


class LiveChatListItem(BaseModel):
    session: ChatSessionResponse


class LiveChatDetailResponse(BaseModel):
    session: ChatSessionResponse
    messages: List[ChatMessageResponse]


class TakeoverResponse(BaseModel):
    session: ChatSessionResponse
    message: ChatMessageResponse


class ReleaseResponse(BaseModel):
    session: ChatSessionResponse
    message: ChatMessageResponse
