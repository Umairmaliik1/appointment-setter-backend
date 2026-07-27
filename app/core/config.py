"""
Core configuration settings for the AI Phone Scheduler SaaS platform.
"""

import os

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Redis settings
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# PostgreSQL settings
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/appointment_setter")
DATABASE_POOL_SIZE = int(os.environ.get("DATABASE_POOL_SIZE", "10"))
DATABASE_POOL_MAX_OVERFLOW = int(os.environ.get("DATABASE_POOL_MAX_OVERFLOW", "20"))
DATABASE_POOL_TIMEOUT_SECONDS = int(os.environ.get("DATABASE_POOL_TIMEOUT_SECONDS", "30"))
DATABASE_CONNECT_TIMEOUT_SECONDS = int(os.environ.get("DATABASE_CONNECT_TIMEOUT_SECONDS", "10"))

# LiveKit settings
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "")
# LiveKit SIP domain for Twilio integration (separate from WebSocket URL)
# Configure SIP Inbound Trunk in LiveKit Cloud and use that domain here
# Example: 3nbu801ppzl.sip.livekit.cloud (NOT the WebSocket domain)
# Get this from LiveKit Cloud Dashboard → SIP → Inbound Trunks
LIVEKIT_SIP_DOMAIN = os.environ.get("LIVEKIT_SIP_DOMAIN", "")

# Redis config TTL for called-number-based lookups (Individual dispatch)
CALL_CONFIG_TTL_SECONDS = int(os.environ.get("CALL_CONFIG_TTL_SECONDS", "3600"))

# LiveKit SIP header mapping for tenant identification
# Tenant ID is the PRIMARY identifier for multi-tenant routing
# Worker extracts tenant_id from SIP headers to load per-call config
LIVEKIT_SIP_HEADER_TENANT_ID = os.environ.get("LIVEKIT_SIP_HEADER_TENANT_ID", "X-LK-TenantId")
LIVEKIT_SIP_ATTRIBUTE_TENANT_ID = os.environ.get("LIVEKIT_SIP_ATTRIBUTE_TENANT_ID", "lk_tenant_id")

# Call ID for per-call isolation (unique per inbound call)
LIVEKIT_SIP_HEADER_CALL_ID = os.environ.get("LIVEKIT_SIP_HEADER_CALL_ID", "X-LK-CallId")
LIVEKIT_SIP_ATTRIBUTE_CALL_ID = os.environ.get("LIVEKIT_SIP_ATTRIBUTE_CALL_ID", "lk_call_id")

# Called number (for logging/reference, not primary lookup)
LIVEKIT_SIP_HEADER_CALLED_NUMBER = os.environ.get("LIVEKIT_SIP_HEADER_CALLED_NUMBER", "X-LK-CalledNumber")
LIVEKIT_SIP_ATTRIBUTE_CALLED_NUMBER = os.environ.get("LIVEKIT_SIP_ATTRIBUTE_CALLED_NUMBER", "lk_called_number")

# Twilio settings
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WEBHOOK_BASE_URL = os.environ.get("TWILIO_WEBHOOK_BASE_URL", "")

# SMS App (cold-SMS outreach) settings
# Base URL Twilio uses to reach the SMS inbound/status webhooks. Falls back to
# TWILIO_WEBHOOK_BASE_URL when unset so both apps can share one public host.
SMS_WEBHOOK_BASE_URL = os.environ.get("SMS_WEBHOOK_BASE_URL", "") or TWILIO_WEBHOOK_BASE_URL
# Default per-tenant outbound throttle (messages per minute) for the drip worker.
SMS_DEFAULT_THROTTLE_PER_MIN = int(os.environ.get("SMS_DEFAULT_THROTTLE_PER_MIN", "60"))
# How many due rows the worker claims per poll iteration.
SMS_WORKER_BATCH_SIZE = int(os.environ.get("SMS_WORKER_BATCH_SIZE", "50"))
# Seconds the worker sleeps between polls when there is no due work.
SMS_WORKER_POLL_INTERVAL_SECONDS = float(os.environ.get("SMS_WORKER_POLL_INTERVAL_SECONDS", "2.0"))
# Max delivery attempts before a scheduled send is marked failed.
SMS_MAX_SEND_ATTEMPTS = int(os.environ.get("SMS_MAX_SEND_ATTEMPTS", "3"))
# Default quiet hours window (local to the campaign timezone), inclusive start / exclusive end (24h).
SMS_QUIET_HOURS_START = int(os.environ.get("SMS_QUIET_HOURS_START", "21"))  # 9pm
SMS_QUIET_HOURS_END = int(os.environ.get("SMS_QUIET_HOURS_END", "9"))  # 9am
# Inbound keywords that trigger automatic opt-out (matched case-insensitively, whole message).
SMS_STOP_KEYWORDS = {
    kw.strip().upper()
    for kw in os.environ.get("SMS_STOP_KEYWORDS", "STOP,STOPALL,UNSUBSCRIBE,CANCEL,END,QUIT").split(",")
    if kw.strip()
}
# Inbound keywords that resume messaging after an opt-out.
SMS_START_KEYWORDS = {
    kw.strip().upper()
    for kw in os.environ.get("SMS_START_KEYWORDS", "START,YES,UNSTOP").split(",")
    if kw.strip()
}

# AI Services — only Google API key is required (Gemini Live).
# OpenAI key kept around since other modules may consult it.
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")


# Email Settings — SMTP via fastapi_mail (see app/services/email/service.py).
class EmailSettings:
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_FROM = os.environ.get("MAIL_FROM", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_STARTTLS = os.environ.get("MAIL_STARTTLS", "True").lower() == "true"
    MAIL_SSL_TLS = os.environ.get("MAIL_SSL_TLS", "False").lower() == "true"
    USE_CREDENTIALS = os.environ.get("USE_CREDENTIALS", "True").lower() == "true"
    VALIDATE_CERTS = os.environ.get("VALIDATE_CERTS", "True").lower() == "true"

email_settings = EmailSettings()

# AWS settings
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Application settings
DEBUG = os.environ.get("DEBUG", "false").lower() in ("true", "1", "t")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
API_HOST = os.environ.get("API_HOST", "0.0.0.0")  # nosec B104 - Intentional: Server needs to bind to all interfaces
API_PORT = int(os.environ.get("API_PORT", "8000"))
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")

# CORS settings
# Comma-separated origins, e.g. "https://app.example.com,https://admin.example.com"
# Use "*" only for local development.
CORS_ALLOW_ORIGINS = os.environ.get("CORS_ALLOW_ORIGINS", "*")

# JWT settings
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
JWT_REFRESH_TOKEN_EXPIRE_DAYS = int(os.environ.get("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))

# Auth cookie settings (for HttpOnly session rollout)
ACCESS_TOKEN_COOKIE_NAME = os.environ.get("ACCESS_TOKEN_COOKIE_NAME", "access_token")
REFRESH_TOKEN_COOKIE_NAME = os.environ.get("REFRESH_TOKEN_COOKIE_NAME", "refresh_token")
AUTH_COOKIE_DOMAIN = os.environ.get("AUTH_COOKIE_DOMAIN", "")
_default_cookie_secure = "false" if ENVIRONMENT == "development" else "true"
AUTH_COOKIE_SECURE = os.environ.get("AUTH_COOKIE_SECURE", _default_cookie_secure).lower() == "true"
AUTH_COOKIE_SAMESITE = os.environ.get("AUTH_COOKIE_SAMESITE", "lax").lower()

# Security settings
SECRET_KEY = os.environ.get("SECRET_KEY", "")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# Chatbot embed settings
CHATBOT_EMBED_SECRET = os.environ.get("CHATBOT_EMBED_SECRET", SECRET_KEY)
CHATBOT_EMBED_TOKEN_TTL_MINUTES = int(os.environ.get("CHATBOT_EMBED_TOKEN_TTL_MINUTES", "60"))
CHATBOT_TEXT_MODEL = os.environ.get("CHATBOT_TEXT_MODEL", "gemini-2.5-flash-lite")
CHATBOT_LOADER_BASE_URL = os.environ.get("CHATBOT_LOADER_BASE_URL", "")
CHATBOT_LLM_PROVIDER = os.environ.get("CHATBOT_LLM_PROVIDER", "gemini")
CHATBOT_STREAM_TIMEOUT_SECONDS = int(os.environ.get("CHATBOT_STREAM_TIMEOUT_SECONDS", "60"))
CHATBOT_DEV_ALLOW_ANY_ORIGIN = os.environ.get("CHATBOT_DEV_ALLOW_ANY_ORIGIN", "false").lower() == "true"
CHATBOT_ALLOW_ANY_ORIGIN = os.environ.get("CHATBOT_ALLOW_ANY_ORIGIN", os.environ.get("CHATBOT_DEV_ALLOW_ANY_ORIGIN", "false")).lower() == "true"
CHATBOT_RUNTIME_ENABLED = os.environ.get("CHATBOT_RUNTIME_ENABLED", "true").lower() == "true"

# MindRind platform branding defaults
PLATFORM_BRAND_NAME = os.environ.get("PLATFORM_BRAND_NAME", "MindRind")
PLATFORM_BRAND_LOGO_URL = os.environ.get("PLATFORM_BRAND_LOGO_URL", "")
PLATFORM_BRAND_PRIMARY_COLOR = os.environ.get("PLATFORM_BRAND_PRIMARY_COLOR", "#0f172a")
PLATFORM_BRAND_SECONDARY_COLOR = os.environ.get("PLATFORM_BRAND_SECONDARY_COLOR", "#ffffff")
PLATFORM_BRAND_ACCENT_COLOR = os.environ.get("PLATFORM_BRAND_ACCENT_COLOR", "#f59e0b")
PLATFORM_APP_BASE_URL = os.environ.get("PLATFORM_APP_BASE_URL", "http://localhost:3000")

