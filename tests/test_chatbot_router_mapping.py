"""
Tests for chatbot router response mapping and legacy schema guardrails.
"""

from fastapi import HTTPException

from app.api.v1.routers.chatbot_agents import _to_chatbot_response


def _base_chatbot_payload():
    return {
        "id": "cb-1",
        "owner_user_id": "user-1",
        "name": "Support Bot",
        "welcome_message": "Hello!",
        "allowed_origins": ["https://example.com"],
        "theme": {"primary_color": "#0EA5E9", "background_color": "#FFFFFF", "text_color": "#111111"},
        "status": "active",
        "domain_key": "customer_support",
        "custom_domain_name": None,
        "behavior_config": {
            "persona": "Support assistant",
            "goal": "Resolve customer questions quickly",
            "tone": "friendly",
            "response_style": "balanced",
            "allowed_topics": ["orders"],
            "blocked_topics": ["medical"],
            "escalation_instructions": "Escalate to human when needed.",
            "custom_instructions": "",
            "language": "en",
        },
        "knowledge_config": {"business_facts": "Returns accepted in 30 days.", "faq_items": []},
        "launcher_config": {
            "mode": "launcher",
            "position": "bottom-right",
            "button_label": "Chat with us",
            "button_icon": "message-circle",
            "accent_color": "#0EA5E9",
        },
        "embed_token_version": 1,
        "created_at": "2026-03-16T00:00:00+00:00",
        "updated_at": "2026-03-16T00:00:00+00:00",
    }


def test_to_chatbot_response_maps_v2_document():
    payload = _base_chatbot_payload()
    response = _to_chatbot_response(payload)
    assert response.id == "cb-1"
    assert response.domain_key == "customer_support"
    assert response.embed_token_version == 1


def test_to_chatbot_response_rejects_legacy_document():
    payload = _base_chatbot_payload()
    del payload["domain_key"]
    del payload["launcher_config"]

    try:
        _to_chatbot_response(payload)
        raise AssertionError("Expected HTTPException for legacy schema")
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "legacy schema" in str(exc.detail).lower()
        assert "migrate_chatbot_agents_v2.py" in str(exc.detail)
