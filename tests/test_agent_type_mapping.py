"""
Tests for agent type defaults and chatbot schema contracts.
"""

from app.api.v1.schemas.chatbot_agent import ChatbotAgentCreate
from app.core.response_mappers import to_agent_response


def test_agent_response_defaults_to_voice_for_legacy_records():
    """Legacy voice agents should return agent_type=voice."""
    response = to_agent_response(
        {
            "id": "agent-1",
            "tenant_id": "tenant-1",
            "name": "Legacy Agent",
            "voice_id": "voice-1",
            "language": "en-US",
            "greeting_message": "Hello there, how can I help you today?",
            "service_type": "Plumbing",
            "status": "active",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    )
    assert response.agent_type == "voice"


def test_chatbot_create_schema_contract():
    """Chatbot create schema should accept required structured fields."""
    payload = ChatbotAgentCreate(
        name="Sales Bot",
        welcome_message="Welcome! Ask me anything.",
        allowed_origins=["https://app.example.com"],
        theme={
            "primary_color": "#0EA5E9",
            "background_color": "#FFFFFF",
            "text_color": "#111111",
        },
        status="active",
        domain_key="customer_support",
        behavior_config={
            "persona": "Support assistant",
            "goal": "Resolve support issues",
            "tone": "friendly",
            "response_style": "balanced",
            "allowed_topics": [],
            "blocked_topics": [],
            "escalation_instructions": "Escalate when needed.",
            "custom_instructions": "",
            "language": "en",
        },
        knowledge_config={
            "business_facts": "We support returns within 30 days.",
            "faq_items": [],
        },
        launcher_config={
            "mode": "launcher",
            "position": "bottom-right",
            "button_label": "Chat with us",
            "button_icon": "message-circle",
            "accent_color": "#0EA5E9",
        },
    )

    assert payload.name == "Sales Bot"
    assert payload.allowed_origins == ["https://app.example.com"]
    assert payload.theme.primary_color == "#0EA5E9"
