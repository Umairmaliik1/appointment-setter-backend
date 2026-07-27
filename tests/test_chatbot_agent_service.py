"""
Tests for chatbot agent service behavior.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

from app.chatbot_agents import service as chatbot_service_module
from app.chatbot_agents.service import ChatbotAgentService


def _chatbot_payload() -> Dict[str, Any]:
    return {
        "id": "cb-1",
        "owner_user_id": "user-1",
        "name": "Sales Bot",
        "welcome_message": "Hello!",
        "theme": {"primary_color": "#0EA5E9", "background_color": "#FFFFFF", "text_color": "#111111"},
        "status": "active",
        "allowed_origins": ["https://allowed.example.com"],
        "domain_key": "customer_support",
        "custom_domain_name": None,
        "behavior_config": {
            "persona": "Support assistant",
            "goal": "Resolve support issues",
            "tone": "friendly",
            "response_style": "balanced",
            "allowed_topics": ["orders", "returns"],
            "blocked_topics": ["medical advice"],
            "escalation_instructions": "Escalate to human support for billing disputes.",
            "custom_instructions": "Always confirm order number.",
            "language": "en",
        },
        "knowledge_config": {
            "business_facts": "We support returns within 30 days.",
            "faq_items": [{"question": "What is return window?", "answer": "30 days from delivery."}],
        },
        "launcher_config": {
            "mode": "launcher",
            "position": "bottom-right",
            "button_label": "Chat with us",
            "button_icon": "message-circle",
            "accent_color": "#0EA5E9",
        },
        "embed_token_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


class _RepoStub:
    def __init__(self, chatbot: Dict[str, Any]):
        self.chatbot = chatbot
        self.logs: List[Dict[str, Any]] = []
        self.runtime_settings = {"enabled": True}

    async def get(self, chatbot_id):
        if chatbot_id == self.chatbot["id"]:
            return self.chatbot
        return None

    async def update(self, chatbot_id, update_data):
        if chatbot_id != self.chatbot["id"]:
            return None
        self.chatbot.update(update_data)
        return self.chatbot

    async def create_runtime_log(self, log_data):
        self.logs.append(log_data)
        return log_data

    async def list_runtime_logs(self, chatbot_id, limit=100, status_filter=None):
        _ = (chatbot_id, limit, status_filter)
        return self.logs

    async def get_runtime_settings(self):
        return self.runtime_settings

    async def update_runtime_settings(self, enabled: bool, updated_by: str):
        self.runtime_settings = {
            "enabled": enabled,
            "updated_by": updated_by,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return self.runtime_settings


class _TokenStub:
    def __init__(self, claims):
        self.claims = claims

    def create_token(self, chatbot_id: str, origin: str, version: int):
        return {"token": f"token-{chatbot_id}", "expires_at": datetime.now(timezone.utc).isoformat(), "ver": version}

    def verify_token(self, token: str):
        _ = token
        return self.claims


class _ProviderStub:
    async def stream_reply(self, payload):
        _ = payload
        yield {"type": "delta", "text": "Hello"}
        yield {"type": "done", "full_text": "Hello"}


@pytest.mark.asyncio
async def test_create_embed_token_rejects_unlisted_origin(monkeypatch):
    monkeypatch.setattr(chatbot_service_module, "ENVIRONMENT", "production")
    monkeypatch.setattr(chatbot_service_module, "CHATBOT_LOADER_BASE_URL", "https://api.example.com/api/v1/chatbot-embed/loader.js")
    service = ChatbotAgentService()
    chatbot = _chatbot_payload()

    with pytest.raises(ValueError, match="not allowed"):
        await service.create_embed_token(chatbot, "https://blocked.example.com")


@pytest.mark.asyncio
async def test_create_embed_token_requires_loader_base(monkeypatch):
    monkeypatch.setattr(chatbot_service_module, "CHATBOT_LOADER_BASE_URL", "")
    service = ChatbotAgentService()
    chatbot = _chatbot_payload()

    with pytest.raises(ValueError, match="CHATBOT_LOADER_BASE_URL"):
        await service.create_embed_token(chatbot, "https://allowed.example.com")


@pytest.mark.asyncio
async def test_create_embed_token_returns_launcher_script(monkeypatch):
    monkeypatch.setattr(chatbot_service_module, "CHATBOT_LOADER_BASE_URL", "https://api.example.com/api/v1/chatbot-embed/loader.js")
    service = ChatbotAgentService()
    chatbot = _chatbot_payload()

    token_data = await service.create_embed_token(chatbot, "https://allowed.example.com")
    assert token_data["loader_url"].startswith("https://api.example.com/api/v1/chatbot-embed/loader.js?token=")
    assert "embed_origin=https%3A%2F%2Fallowed.example.com" in token_data["loader_url"]
    assert token_data["token_version"] == 2
    assert token_data["launcher_script"].startswith("<script")


@pytest.mark.asyncio
async def test_get_public_embed_config_rejects_revoked_token(monkeypatch):
    monkeypatch.setattr(chatbot_service_module, "ENVIRONMENT", "production")
    chatbot = _chatbot_payload()
    claims = {"sub": "cb-1", "origin": "https://allowed.example.com", "type": "chatbot_embed", "ver": 1}

    service = ChatbotAgentService()
    service.repository = _RepoStub(chatbot)
    service.token_service = _TokenStub(claims)

    with pytest.raises(ValueError, match="revoked"):
        await service.get_public_embed_config(token="dummy", request_origin="https://allowed.example.com")


def test_build_system_instruction_includes_template_and_behavior():
    service = ChatbotAgentService()
    chatbot = _chatbot_payload()

    instruction = service._build_system_instruction(chatbot)
    assert "Domain baseline" in instruction
    assert "Support assistant" in instruction
    assert "returns within 30 days" in instruction


@pytest.mark.asyncio
async def test_stream_chat_reply_emits_events_and_logs():
    service = ChatbotAgentService()
    chatbot = _chatbot_payload()
    repo = _RepoStub(chatbot)
    service.repository = repo
    service.llm_provider = _ProviderStub()

    events = []
    async for chunk in service.stream_chat_reply(
        chatbot=chatbot, message="Hi", history=[], request_id="req-1", client_ip="127.0.0.1"
    ):
        events.append(chunk)

    assert any('"type": "delta"' in chunk for chunk in events)
    assert any('"type": "done"' in chunk for chunk in events)
    assert repo.logs


def test_can_manage_owner_and_admin():
    service = ChatbotAgentService()
    chatbot = {"id": "cb-1", "owner_user_id": "user-1"}

    assert service.can_manage(chatbot, user_id="user-1", role="user")
    assert service.can_manage(chatbot, user_id="user-2", role="admin")
    assert not service.can_manage(chatbot, user_id="user-2", role="user")
