"""
Agent service layer for business logic.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.api.v1.schemas.agent import AgentCreate, AgentUpdate, VoiceOption
from app.services.store import store

logger = logging.getLogger(__name__)


class AgentService:
    """Service class for agent operations."""

    def __init__(self):
        """Initialize agent service."""
        pass

    @staticmethod
    def _ensure_voice_agent_type(agent: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Apply read-time fallback for legacy records without agent_type."""
        if not agent:
            return agent
        if not agent.get("agent_type"):
            agent = {**agent, "agent_type": "voice"}
        return agent

    async def create_agent(self, tenant_id: str, agent_data: AgentCreate) -> Dict[str, Any]:
        """Create a new agent for a tenant."""
        agent_dict = {
            "id": str(uuid.uuid4()),
            "agent_type": "voice",
            "tenant_id": tenant_id,
            "name": agent_data.name,
            "voice_id": agent_data.voice_id,
            "language": agent_data.language,
            "greeting_message": agent_data.greeting_message,
            "service_type": agent_data.service_type,
            "system_prompt": (agent_data.system_prompt or "").strip(),
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        created = await store.create_agent(agent_dict)
        return self._ensure_voice_agent_type(created)

    async def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get agent by ID."""
        agent = await store.get_agent(agent_id)
        return self._ensure_voice_agent_type(agent)

    async def list_agents_by_tenant(self, tenant_id: str) -> List[Dict[str, Any]]:
        """List all agents for a tenant."""
        agents = await store.list_agents_by_tenant(tenant_id)
        return [self._ensure_voice_agent_type(agent) for agent in agents]

    async def update_agent(self, agent_id: str, agent_data: AgentUpdate) -> Optional[Dict[str, Any]]:
        """Update agent."""
        agent = await self.get_agent(agent_id)
        if not agent:
            return None

        update_data = {"updated_at": datetime.now(timezone.utc).isoformat(), "agent_type": "voice"}

        if agent_data.name is not None:
            update_data["name"] = agent_data.name
        if agent_data.voice_id is not None:
            update_data["voice_id"] = agent_data.voice_id
        if agent_data.language is not None:
            update_data["language"] = agent_data.language
        if agent_data.greeting_message is not None:
            update_data["greeting_message"] = agent_data.greeting_message
        if agent_data.service_type is not None:
            update_data["service_type"] = agent_data.service_type
        if agent_data.system_prompt is not None:
            update_data["system_prompt"] = agent_data.system_prompt.strip()
        if agent_data.status is not None:
            update_data["status"] = agent_data.status

        updated = await store.update_agent(agent_id, update_data)
        return self._ensure_voice_agent_type(updated)

    async def delete_agent(self, agent_id: str) -> bool:
        """Delete an agent and free every phone number attached to it.

        Production behavior:
        - Any phone numbers currently assigned to this agent are unassigned
          (agent_id cleared, status/role_status set to inactive) BEFORE the
          agent row is deleted. This mirrors `unassign_phone_from_agent` so
          freed numbers reappear in the tenant's "Unassigned Numbers" pool
          and can be reattached to another agent.
        - We do NOT delete the phone_number rows themselves — the tenant
          still owns those Twilio numbers and should be able to reuse them.
        - Historical records (appointments, call logs) are preserved on
          purpose; only the live binding is cleaned up.
        - If freeing the phone numbers fails, we abort the delete so the
          system never ends up with orphan rows pointing at a missing agent.
        """
        agent = await store.get_agent(agent_id)
        if not agent:
            return False

        tenant_id = agent.get("tenant_id")
        try:
            attached_phones = await self._list_phones_assigned_to_agent(tenant_id, agent_id)
        except Exception:
            logger.exception(
                "[AGENT DELETE] Failed to enumerate attached phone numbers for agent %s; aborting delete",
                agent_id,
            )
            raise

        for phone in attached_phones:
            phone_id = phone.get("id")
            if not phone_id:
                continue
            try:
                await store.update_phone_number(
                    phone_id,
                    {
                        "agent_id": "",
                        "status": "inactive",
                        "role_status": "inactive",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                logger.info(
                    "[AGENT DELETE] Unassigned phone %s (%s) from agent %s before delete",
                    phone_id,
                    phone.get("phone_number"),
                    agent_id,
                )
            except Exception:
                logger.exception(
                    "[AGENT DELETE] Failed to unassign phone %s from agent %s; aborting delete",
                    phone_id,
                    agent_id,
                )
                raise

        return await store.delete_agent(agent_id)

    @staticmethod
    async def _list_phones_assigned_to_agent(
        tenant_id: Optional[str], agent_id: str
    ) -> List[Dict[str, Any]]:
        """Return every phone_number row currently bound to this agent."""
        if not tenant_id:
            return []
        phones = await store.list_phones_by_tenant(tenant_id) or []
        return [p for p in phones if p.get("agent_id") == agent_id]

    async def activate_agent(self, agent_id: str) -> bool:
        """Activate an agent."""
        update_data = {"status": "active", "agent_type": "voice", "updated_at": datetime.now(timezone.utc).isoformat()}
        result = await store.update_agent(agent_id, update_data)
        return result is not None

    async def deactivate_agent(self, agent_id: str) -> bool:
        """Deactivate an agent."""
        update_data = {"status": "inactive", "agent_type": "voice", "updated_at": datetime.now(timezone.utc).isoformat()}
        result = await store.update_agent(agent_id, update_data)
        return result is not None

    def get_available_voices(self) -> List[VoiceOption]:
        """Return the voice catalog the agent can speak with.

        Gemini Live ships a fixed catalog of 30 prebuilt voices; that's the
        list, no filtering needed.
        """
        from app.core.voice_metadata import get_all_voices

        return [
            VoiceOption(
                voice_id=voice["voice_id"],
                name=voice["name"],
                description=voice["description"],
                category=voice["category"],
                use_case=voice["use_case"],
            )
            for voice in get_all_voices()
        ]


# Global agent service instance
agent_service = AgentService()
