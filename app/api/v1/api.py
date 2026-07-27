"""
API v1 router configuration.
"""

from fastapi import APIRouter

from app.api.v1.routers import (
    agents,
    appointments,
    auth,
    chatbot_agents,
    chatbot_embed,
    contacts,
    health,
    orgs,
    phone_numbers,
    platform,
    sms,
    telephony,
    tenants,
    twilio_integration,
    voice_agent_unified,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(platform.router)
api_router.include_router(tenants.router)
api_router.include_router(agents.router)
api_router.include_router(phone_numbers.router)
api_router.include_router(telephony.router)
api_router.include_router(appointments.router)
api_router.include_router(platform.router)
api_router.include_router(orgs.router)
api_router.include_router(voice_agent_unified.router)
api_router.include_router(twilio_integration.router)
api_router.include_router(contacts.router)
api_router.include_router(chatbot_agents.router)
api_router.include_router(chatbot_embed.router)
api_router.include_router(sms.router)
api_router.include_router(sms.webhook_router)
