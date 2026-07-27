"""
Tests for phone assignment agent_type safety guard.
"""

import pytest

from app.api.v1.services.phone_number import phone_number_service


@pytest.mark.asyncio
async def test_phone_assignment_rejects_non_voice_agent(monkeypatch):
    """Phone assignment must reject chatbot agent IDs."""

    async def _fake_get_agent(agent_id):
        return {
            "id": agent_id,
            "tenant_id": "tenant-1",
            "name": "Chat Bot",
            "agent_type": "chatbot",
            "service_type": "Plumbing",
        }

    monkeypatch.setattr("app.api.v1.services.phone_number.store.get_agent", _fake_get_agent)

    with pytest.raises(ValueError, match="only be assigned to voice agents"):
        await phone_number_service._validate_agent_for_tenant("agent-1", "tenant-1")


@pytest.mark.asyncio
async def test_phone_assignment_allows_legacy_agent_without_type(monkeypatch):
    """Legacy agents missing agent_type are treated as voice."""

    async def _fake_get_agent(agent_id):
        return {
            "id": agent_id,
            "tenant_id": "tenant-1",
            "name": "Legacy Voice Agent",
            "service_type": "Plumbing",
        }

    monkeypatch.setattr("app.api.v1.services.phone_number.store.get_agent", _fake_get_agent)

    result = await phone_number_service._validate_agent_for_tenant("agent-1", "tenant-1")
    assert result["name"] == "Legacy Voice Agent"

