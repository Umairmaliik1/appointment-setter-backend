"""Tests for chatbot embed loader/panel runtime scripts."""

import pytest

from app.api.v1.routers import chatbot_embed as chatbot_embed_router


class _DummyClient:
    host = "127.0.0.1"


class _DummyRequest:
    client = _DummyClient()

    @staticmethod
    def url_for(name: str) -> str:
        assert name == "get_chatbot_embed_panel"
        return "https://api.example.com/api/v1/chatbot-embed/panel"


@pytest.mark.asyncio
async def test_chatbot_embed_panel_includes_session_bootstrap_runtime():
    response = await chatbot_embed_router.get_chatbot_embed_panel(
        token="token-123",
        embed_origin=None,
        page_url=None,
        page_title=None,
    )
    body = response.body.decode("utf-8")

    assert "samai-chatbot-visitor:" in body
    assert "create chat session" in body.lower()
    assert "new URL('./sessions', window.location.href)" in body
    assert "new WebSocket(wsUrl.toString())" in body
    assert "A team member joined the chat" in body
    assert "payload.type === 'takeover.released'" in body


@pytest.mark.asyncio
async def test_chatbot_embed_loader_passes_page_context_into_iframe(monkeypatch):
    async def _noop_rate_limit(*_args, **_kwargs):
        return None

    async def _fake_get_public_embed_config(token: str, request_origin: str):
        assert token == "token-abc"
        assert request_origin == "https://site.example.com"
        return {"chatbot_id": "cb-1"}

    async def _fake_get_chatbot_agent(chatbot_id: str):
        assert chatbot_id == "cb-1"
        return {
            "launcher_config": {
                "position": "bottom-right",
                "button_label": "Chat with us",
                "accent_color": "#0EA5E9",
            }
        }

    monkeypatch.setattr(chatbot_embed_router, "_enforce_public_rate_limit", _noop_rate_limit)
    monkeypatch.setattr(chatbot_embed_router.chatbot_agent_service, "get_public_embed_config", _fake_get_public_embed_config)
    monkeypatch.setattr(chatbot_embed_router.chatbot_agent_service, "get_chatbot_agent", _fake_get_chatbot_agent)

    response = await chatbot_embed_router.get_chatbot_embed_loader(
        request=_DummyRequest(),
        token="token-abc",
        embed_origin=None,
        origin="https://site.example.com",
        referer=None,
    )
    script = response.body.decode("utf-8")

    assert "iframeUrl.searchParams.set('page_url', window.location.href || '');" in script
    assert "iframeUrl.searchParams.set('page_title', document.title || '');" in script
    assert "button.addEventListener('click'" in script
