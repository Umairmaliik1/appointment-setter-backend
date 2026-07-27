"""
Tests for chatbot live chat takeover behavior.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from app.chatbot_agents import live_chat_service as live_chat_module
from app.chatbot_agents.live_chat_service import ChatbotLiveChatService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chatbot():
    return {
        "id": "cb-1",
        "owner_user_id": "owner-1",
        "name": "Support Bot",
        "welcome_message": "Hello from bot",
        "status": "active",
        "allowed_origins": ["https://site.example.com"],
        "domain_key": "customer_support",
        "custom_domain_name": None,
        "behavior_config": {
            "persona": "Support assistant",
            "goal": "Help users",
            "tone": "friendly",
            "response_style": "balanced",
            "allowed_topics": [],
            "blocked_topics": [],
            "escalation_instructions": "Escalate when needed.",
            "custom_instructions": "",
            "language": "en",
        },
        "knowledge_config": {"business_facts": "We are open weekdays.", "faq_items": []},
        "launcher_config": {
            "mode": "launcher",
            "position": "bottom-right",
            "button_label": "Chat",
            "button_icon": "message-circle",
            "accent_color": "#0EA5E9",
        },
        "embed_token_version": 1,
        "created_at": _now(),
        "updated_at": _now(),
    }


class _RepoStub:
    def __init__(self):
        self.sessions = {}
        self.messages = {}

    async def create_chat_session(self, session_data):
        self.sessions[session_data["id"]] = dict(session_data)
        return dict(self.sessions[session_data["id"]])

    async def get_chat_session(self, session_id):
        session = self.sessions.get(session_id)
        return dict(session) if session else None

    async def update_chat_session(self, session_id, update_data):
        self.sessions[session_id].update(update_data)
        return dict(self.sessions[session_id])

    async def find_open_chat_session(self, chatbot_id, visitor_session_id, origin):
        for session in self.sessions.values():
            if (
                session["chatbot_id"] == chatbot_id
                and session["visitor_session_id"] == visitor_session_id
                and session["origin"] == origin
                and session["status"] == "open"
            ):
                return dict(session)
        return None

    async def list_chat_messages(self, session_id, limit=250):
        return [dict(entry) for entry in self.messages.get(session_id, [])][:limit]

    async def create_chat_message(self, message_data):
        self.messages.setdefault(message_data["session_id"], []).append(dict(message_data))
        return dict(message_data)

    async def list_chat_sessions_for_owner(self, owner_user_id, limit=100):
        entries = [dict(session) for session in self.sessions.values() if session["owner_user_id"] == owner_user_id]
        entries.sort(key=lambda item: item.get("last_activity_at") or "", reverse=True)
        return entries[:limit]

    async def list_chat_sessions(self, limit=100):
        entries = [dict(session) for session in self.sessions.values()]
        entries.sort(key=lambda item: item.get("last_activity_at") or "", reverse=True)
        return entries[:limit]


@pytest.mark.asyncio
async def test_create_or_restore_session_creates_new_session(monkeypatch):
    repo = _RepoStub()
    service = ChatbotLiveChatService()
    service.repository = repo

    async def _fake_get_public_embed_config(token, request_origin):
        assert token == "embed-token"
        assert request_origin == "https://site.example.com"
        return {"chatbot_id": "cb-1"}

    async def _fake_get_chatbot_agent(chatbot_id):
        assert chatbot_id == "cb-1"
        return _chatbot()

    monkeypatch.setattr(live_chat_module.chatbot_agent_service, "get_public_embed_config", _fake_get_public_embed_config)
    monkeypatch.setattr(live_chat_module.chatbot_agent_service, "get_chatbot_agent", _fake_get_chatbot_agent)
    monkeypatch.setattr(
        live_chat_module.chatbot_embed_token_service,
        "create_session_token",
        lambda session_id, chatbot_id, origin, visitor_session_id: {"token": f"session-{session_id}"},
    )
    monkeypatch.setattr(service, "_publish_event", lambda *_args, **_kwargs: asyncio.sleep(0))

    payload = await service.create_or_restore_session(
        token="embed-token",
        request_origin="https://site.example.com",
        visitor_session_id="visitor-123",
        page_url="https://site.example.com/pricing",
        page_title="Pricing",
    )

    assert payload["session"]["visitor_label"] == "Visitor visito"
    assert payload["session"]["control_mode"] == "bot"
    assert payload["session_token"].startswith("session-")
    assert payload["messages"][0]["sender_type"] == "bot"
    assert payload["messages"][0]["content"] == "Hello from bot"


@pytest.mark.asyncio
async def test_create_or_restore_session_reuses_existing_session(monkeypatch):
    repo = _RepoStub()
    session = {
        "id": "session-1",
        "chatbot_id": "cb-1",
        "owner_user_id": "owner-1",
        "origin": "https://site.example.com",
        "page_url": "https://site.example.com/old",
        "page_title": "Old",
        "visitor_session_id": "visitor-123",
        "visitor_label": "Visitor abc123",
        "status": "open",
        "control_mode": "bot",
        "assigned_operator_id": None,
        "assigned_operator_name": None,
        "started_at": _now(),
        "last_activity_at": _now(),
        "taken_over_at": None,
        "released_at": None,
        "closed_at": None,
        "last_message_preview": "Hello",
    }
    repo.sessions[session["id"]] = dict(session)
    repo.messages[session["id"]] = [
        {"id": "m-1", "session_id": session["id"], "chatbot_id": "cb-1", "sender_type": "bot", "sender_id": None, "content": "Hello", "created_at": _now()}
    ]

    service = ChatbotLiveChatService()
    service.repository = repo

    async def _fake_get_public_embed_config(*_args, **_kwargs):
        return {"chatbot_id": "cb-1"}

    async def _fake_get_chatbot_agent(_chatbot_id):
        return _chatbot()

    monkeypatch.setattr(live_chat_module.chatbot_agent_service, "get_public_embed_config", _fake_get_public_embed_config)
    monkeypatch.setattr(live_chat_module.chatbot_agent_service, "get_chatbot_agent", _fake_get_chatbot_agent)
    monkeypatch.setattr(
        live_chat_module.chatbot_embed_token_service,
        "create_session_token",
        lambda session_id, chatbot_id, origin, visitor_session_id: {"token": f"session-{session_id}"},
    )

    payload = await service.create_or_restore_session(
        token="embed-token",
        request_origin="https://site.example.com",
        visitor_session_id="visitor-123",
        page_url="https://site.example.com/new",
        page_title="New page",
    )

    assert payload["session"]["id"] == "session-1"
    assert payload["session"]["page_url"] == "https://site.example.com/new"
    assert payload["session"]["page_title"] == "New page"
    assert len(payload["messages"]) == 1


@pytest.mark.asyncio
async def test_takeover_rejects_conflict_and_release_returns_to_bot(monkeypatch):
    repo = _RepoStub()
    session = {
        "id": "session-1",
        "chatbot_id": "cb-1",
        "owner_user_id": "owner-1",
        "origin": "https://site.example.com",
        "page_url": "https://site.example.com/page",
        "page_title": "Page",
        "visitor_session_id": "visitor-123",
        "visitor_label": "Visitor abc123",
        "status": "open",
        "control_mode": "human",
        "assigned_operator_id": "other-user",
        "assigned_operator_name": "Other User",
        "started_at": _now(),
        "last_activity_at": _now(),
        "taken_over_at": _now(),
        "released_at": None,
        "closed_at": None,
        "last_message_preview": "Need help",
    }
    repo.sessions[session["id"]] = dict(session)
    repo.messages[session["id"]] = []

    service = ChatbotLiveChatService()
    service.repository = repo
    monkeypatch.setattr(service, "_publish_event", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(service, "_acquire_takeover_lock", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(service, "_release_takeover_lock", lambda *_args, **_kwargs: asyncio.sleep(0))

    async def _fake_get_chatbot_agent(_chatbot_id):
        return _chatbot()

    monkeypatch.setattr(live_chat_module.chatbot_agent_service, "get_chatbot_agent", _fake_get_chatbot_agent)

    with pytest.raises(RuntimeError, match="already assigned"):
      await service.take_over_chat(
            "session-1",
            {"id": "owner-1", "role": "user", "first_name": "Owner", "last_name": "User", "email": "owner@example.com"},
        )

    repo.sessions["session-1"]["assigned_operator_id"] = "owner-1"
    repo.sessions["session-1"]["assigned_operator_name"] = "Owner User"
    released = await service.release_chat(
        "session-1",
        {"id": "owner-1", "role": "user", "first_name": "Owner", "last_name": "User", "email": "owner@example.com"},
    )

    assert released["session"]["control_mode"] == "bot"
    assert released["session"]["assigned_operator_id"] is None
    assert released["message"]["sender_type"] == "system"
    assert "back in the conversation" in released["message"]["content"]


@pytest.mark.asyncio
async def test_create_visitor_message_schedules_bot_reply_in_bot_mode(monkeypatch):
    repo = _RepoStub()
    session = {
        "id": "session-1",
        "chatbot_id": "cb-1",
        "owner_user_id": "owner-1",
        "origin": "https://site.example.com",
        "page_url": "https://site.example.com/page",
        "page_title": "Page",
        "visitor_session_id": "visitor-123",
        "visitor_label": "Visitor abc123",
        "status": "open",
        "control_mode": "bot",
        "assigned_operator_id": None,
        "assigned_operator_name": None,
        "started_at": _now(),
        "last_activity_at": _now(),
        "taken_over_at": None,
        "released_at": None,
        "closed_at": None,
        "last_message_preview": "Hello",
    }
    repo.sessions[session["id"]] = dict(session)
    repo.messages[session["id"]] = []

    service = ChatbotLiveChatService()
    service.repository = repo
    monkeypatch.setattr(service, "_publish_event", lambda *_args, **_kwargs: asyncio.sleep(0))

    async def _fake_get_public_embed_config(*_args, **_kwargs):
        return {"chatbot_id": "cb-1"}

    monkeypatch.setattr(live_chat_module.chatbot_agent_service, "get_public_embed_config", _fake_get_public_embed_config)

    scheduled = []

    def _fake_create_task(coro):
        scheduled.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(live_chat_module.asyncio, "create_task", _fake_create_task)

    message = await service.create_visitor_message(
        session_id="session-1",
        token="embed-token",
        request_origin="https://site.example.com",
        visitor_session_id="visitor-123",
        message="I need help",
    )

    assert message["sender_type"] == "visitor"
    assert message["content"] == "I need help"
    assert scheduled


@pytest.mark.asyncio
async def test_create_visitor_message_skips_bot_reply_when_human_has_taken_over(monkeypatch):
    repo = _RepoStub()
    session = {
        "id": "session-2",
        "chatbot_id": "cb-1",
        "owner_user_id": "owner-1",
        "origin": "https://site.example.com",
        "page_url": "https://site.example.com/page",
        "page_title": "Page",
        "visitor_session_id": "visitor-456",
        "visitor_label": "Visitor def456",
        "status": "open",
        "control_mode": "human",
        "assigned_operator_id": "owner-1",
        "assigned_operator_name": "Owner User",
        "started_at": _now(),
        "last_activity_at": _now(),
        "taken_over_at": _now(),
        "released_at": None,
        "closed_at": None,
        "last_message_preview": "Hello",
    }
    repo.sessions[session["id"]] = dict(session)
    repo.messages[session["id"]] = []

    service = ChatbotLiveChatService()
    service.repository = repo
    monkeypatch.setattr(service, "_publish_event", lambda *_args, **_kwargs: asyncio.sleep(0))

    async def _fake_get_public_embed_config(*_args, **_kwargs):
        return {"chatbot_id": "cb-1"}

    monkeypatch.setattr(live_chat_module.chatbot_agent_service, "get_public_embed_config", _fake_get_public_embed_config)

    scheduled = []

    def _fake_create_task(coro):
        scheduled.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(live_chat_module.asyncio, "create_task", _fake_create_task)

    message = await service.create_visitor_message(
        session_id="session-2",
        token="embed-token",
        request_origin="https://site.example.com",
        visitor_session_id="visitor-456",
        message="Can a human help me?",
    )

    assert message["sender_type"] == "visitor"
    assert not scheduled
