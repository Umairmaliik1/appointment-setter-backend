"""
Phone number service layer for business logic.
"""

import uuid
from typing import Any, Dict, List, Optional

from app.api.v1.schemas.phone_number import PhoneNumberCreate, PhoneNumberUpdate
from app.core.utils import add_timestamps
from app.services.store import store
from app.utils.phone_number import normalize_phone_number, normalize_phone_number_safe

VOICE_AGENT_INBOUND_ROLE = "voice_agent_inbound"
COLD_CALLER_OUTBOUND_ROLE = "cold_caller_outbound"
ROLE_STATUS_ACTIVE = "active"
ROLE_STATUS_INACTIVE = "inactive"
ROLE_STATUS_CONFLICT = "conflict"
CONFLICT_ROLE_COLLISION = "ROLE_COLLISION"


class PhoneNumberService:
    """Service class for phone number operations."""

    def __init__(self):
        """Initialize phone number service."""
        pass

    def _normalize_phone_role_fields(self, phone: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize role and conflict fields for deterministic API responses."""
        normalized = dict(phone)
        usage_role = normalized.get("usage_role") or VOICE_AGENT_INBOUND_ROLE
        status = normalized.get("status", "active")
        role_status = normalized.get("role_status")
        conflict_code = normalized.get("conflict_code")
        conflict_message = normalized.get("conflict_message")

        if not role_status:
            role_status = ROLE_STATUS_ACTIVE if status == "active" else ROLE_STATUS_INACTIVE

        if usage_role == COLD_CALLER_OUTBOUND_ROLE and normalized.get("agent_id"):
            role_status = ROLE_STATUS_CONFLICT
            conflict_code = CONFLICT_ROLE_COLLISION
            conflict_message = (
                "Number cannot be cold-caller outbound while also assigned to a voice agent."
            )
        elif role_status != ROLE_STATUS_CONFLICT:
            conflict_code = None
            conflict_message = None

        normalized["usage_role"] = usage_role
        normalized["role_status"] = role_status
        normalized["conflict_code"] = conflict_code
        normalized["conflict_message"] = conflict_message
        return normalized

    def _assert_voice_assignment_allowed(self, phone: Dict[str, Any], phone_number: str) -> None:
        """Hard block assigning a cold-caller outbound number to a voice agent."""
        usage_role = (phone.get("usage_role") or VOICE_AGENT_INBOUND_ROLE).strip()
        if usage_role == COLD_CALLER_OUTBOUND_ROLE:
            raise ValueError(
                f"Phone number {phone_number} is bound to Cold Caller outbound. "
                "Unbind it in Telephony Hub before assigning it to a voice agent."
            )

    async def _validate_agent_for_tenant(
        self, agent_id: str, tenant_id: str, agent: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Validate agent exists, belongs to tenant, and has required fields.

        Args:
            agent_id: Agent identifier
            tenant_id: Tenant identifier
            agent: Optional pre-fetched agent data

        Returns:
            Agent data dictionary

        Raises:
            ValueError: If validation fails
        """
        if not agent:
            agent = await store.get_agent(agent_id)

        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        if agent.get("tenant_id") != tenant_id:
            raise ValueError(f"Agent {agent_id} does not belong to this tenant")

        # Voice safety: phone assignments are only valid for voice agents.
        # Legacy records without agent_type are treated as voice during migration.
        agent_type = agent.get("agent_type") or "voice"
        if agent_type != "voice":
            raise ValueError(
                f"Agent {agent_id} (name: {agent.get('name')}) is type '{agent_type}'. "
                "Phone numbers can only be assigned to voice agents."
            )

        # Verify agent has service_type set (required field)
        if not agent.get("service_type"):
            raise ValueError(
                f"Agent {agent_id} (name: {agent.get('name')}) has no service_type set. "
                f"Please configure the agent's service_type before assigning a phone number."
            )

        return agent

    async def create_phone_number(self, tenant_id: str, phone_data: PhoneNumberCreate) -> Dict[str, Any]:
        """Create a new phone number assignment."""
        # Normalize phone number to E.164 format for consistent storage
        normalized_phone = normalize_phone_number(phone_data.phone_number)

        # Check if phone number is already used (try normalized first, then original)
        existing_phone = await self.get_phone_by_number(normalized_phone)
        if not existing_phone:
            existing_phone = await self.get_phone_by_number(phone_data.phone_number)

        if existing_phone:
            raise ValueError(f"Phone number {normalized_phone} is already assigned")

        # Validate agent
        await self._validate_agent_for_tenant(phone_data.agent_id, tenant_id)

        # Verify twilio integration exists and belongs to tenant
        twilio_integration = await store.get_twilio_integration(tenant_id)
        if not twilio_integration:
            raise ValueError("Twilio integration not found for this tenant")

        # Store normalized phone number
        phone_dict = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "phone_number": normalized_phone,  # Store normalized format
            "agent_id": phone_data.agent_id,
            "twilio_integration_id": phone_data.twilio_integration_id,
            "status": "active",
            "usage_role": VOICE_AGENT_INBOUND_ROLE,
            "role_status": ROLE_STATUS_ACTIVE,
            "conflict_code": None,
            "conflict_message": None,
        }
        add_timestamps(phone_dict)

        created = await store.create_phone_number(phone_dict)
        return self._normalize_phone_role_fields(created)

    async def get_phone_number(self, phone_id: str) -> Optional[Dict[str, Any]]:
        """Get phone number by ID."""
        phone = await store.get_phone_number(phone_id)
        return self._normalize_phone_role_fields(phone) if phone else None

    async def get_phone_by_number(self, phone_number: str) -> Optional[Dict[str, Any]]:
        """
        Get phone number by actual number string.
        Tries normalized format first, then original format.
        """
        # Try normalized format first (most common)
        normalized = normalize_phone_number_safe(phone_number)
        if normalized:
            phone_record = await store.get_phone_by_number(normalized)
            if phone_record:
                return self._normalize_phone_role_fields(phone_record)

        # Fallback to original format (for backwards compatibility)
        phone_record = await store.get_phone_by_number(phone_number)
        return self._normalize_phone_role_fields(phone_record) if phone_record else None

    async def get_phone_by_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get phone number assigned to an agent."""
        phone = await store.get_phone_by_agent(agent_id)
        return self._normalize_phone_role_fields(phone) if phone else None

    async def list_phones_by_tenant(self, tenant_id: str) -> List[Dict[str, Any]]:
        """List all phone numbers for a tenant."""
        phones = await store.list_phones_by_tenant(tenant_id)

        # Enrich with agent names
        normalized_phones: List[Dict[str, Any]] = []
        for phone in phones:
            phone = self._normalize_phone_role_fields(phone)
            if phone.get("agent_id"):
                agent = await store.get_agent(phone["agent_id"])
                if agent:
                    phone["agent_name"] = agent.get("name")
            normalized_phones.append(phone)

        return normalized_phones

    async def update_phone_number(self, phone_id: str, phone_data: PhoneNumberUpdate) -> Optional[Dict[str, Any]]:
        """Update phone number assignment."""
        phone = await self.get_phone_number(phone_id)
        if not phone:
            return None

        update_data = {}

        if phone_data.phone_number is not None:
            # Normalize phone number before checking/updating
            normalized_phone = normalize_phone_number(phone_data.phone_number)
            # Check if new phone number is already used
            existing_phone = await self.get_phone_by_number(normalized_phone)
            if existing_phone and existing_phone["id"] != phone_id:
                raise ValueError(f"Phone number {normalized_phone} is already assigned")
            update_data["phone_number"] = normalized_phone  # Store normalized format

        if phone_data.agent_id is not None:
            self._assert_voice_assignment_allowed(phone, phone.get("phone_number", ""))
            # Validate agent
            await self._validate_agent_for_tenant(phone_data.agent_id, phone.get("tenant_id"))
            update_data["agent_id"] = phone_data.agent_id
            update_data["status"] = "active"
            update_data["usage_role"] = VOICE_AGENT_INBOUND_ROLE
            update_data["role_status"] = ROLE_STATUS_ACTIVE
            update_data["conflict_code"] = None
            update_data["conflict_message"] = None

        if phone_data.status is not None:
            update_data["status"] = phone_data.status
            if phone.get("usage_role") == COLD_CALLER_OUTBOUND_ROLE:
                update_data["role_status"] = ROLE_STATUS_ACTIVE if phone_data.status == "active" else ROLE_STATUS_INACTIVE
            elif "role_status" not in update_data:
                update_data["role_status"] = ROLE_STATUS_ACTIVE if phone_data.status == "active" else ROLE_STATUS_INACTIVE

        # Add updated_at timestamp
        from app.core.utils import add_updated_timestamp

        add_updated_timestamp(update_data)

        updated = await store.update_phone_number(phone_id, update_data)
        return self._normalize_phone_role_fields(updated) if updated else None

    async def delete_phone_number(self, phone_id: str) -> bool:
        """Delete phone number assignment."""
        return await store.delete_phone_number(phone_id)

    async def assign_phone_to_agent(
        self, tenant_id: str, agent_id: str, phone_number: str, twilio_integration_id: str
    ) -> Dict[str, Any]:
        """
        Assign a phone number to an agent.

        Normalizes phone number to E.164 format before storing.
        """
        # Normalize phone number first
        normalized_phone = normalize_phone_number(phone_number)

        # Validate agent
        await self._validate_agent_for_tenant(agent_id, tenant_id)

        # Check if phone number already assigned (using normalized format)
        existing_phone = await self.get_phone_by_number(normalized_phone)
        if existing_phone:
            # Verify existing phone belongs to the same tenant
            if existing_phone.get("tenant_id") != tenant_id:
                raise ValueError(f"Phone number {normalized_phone} is already assigned to a different tenant")
            self._assert_voice_assignment_allowed(existing_phone, normalized_phone)
            # Update existing assignment with normalized phone number
            phone_data = PhoneNumberUpdate(agent_id=agent_id, phone_number=normalized_phone)
            return await self.update_phone_number(existing_phone["id"], phone_data)
        else:
            # Create new assignment with normalized phone number
            phone_data = PhoneNumberCreate(
                phone_number=normalized_phone, agent_id=agent_id, twilio_integration_id=twilio_integration_id
            )
            return await self.create_phone_number(tenant_id, phone_data)

    async def unassign_phone_from_agent(self, agent_id: str) -> bool:
        """Remove phone number assignment from an agent."""
        phone = await self.get_phone_by_agent(agent_id)
        if phone:
            updated = await store.update_phone_number(
                phone["id"],
                {
                    "agent_id": "",
                    "status": "inactive",
                    "usage_role": VOICE_AGENT_INBOUND_ROLE,
                    "role_status": ROLE_STATUS_INACTIVE,
                    "conflict_code": None,
                    "conflict_message": None,
                },
            )
            return bool(updated)
        return False

    async def bind_cold_caller_outbound_number(self, tenant_id: str, phone_number: str) -> Dict[str, Any]:
        """Bind a tenant-owned number for Cold Caller outbound usage."""
        normalized_phone = normalize_phone_number(phone_number)
        phone = await self.get_phone_by_number(normalized_phone)
        if not phone or phone.get("tenant_id") != tenant_id:
            raise ValueError(f"Phone number {normalized_phone} not found for this tenant")

        if phone.get("agent_id"):
            raise ValueError(
                f"Phone number {normalized_phone} is already assigned to a voice agent. "
                "Same number cannot be active in both services."
            )

        if phone.get("usage_role") == COLD_CALLER_OUTBOUND_ROLE and phone.get("role_status") == ROLE_STATUS_ACTIVE:
            return phone

        updated = await store.update_phone_number(
            phone["id"],
            {
                "status": "active",
                "usage_role": COLD_CALLER_OUTBOUND_ROLE,
                "role_status": ROLE_STATUS_ACTIVE,
                "conflict_code": None,
                "conflict_message": None,
                "agent_id": "",
            },
        )
        if not updated:
            raise ValueError("Failed to bind outbound number")
        return self._normalize_phone_role_fields(updated)

    async def unbind_cold_caller_outbound_number(self, tenant_id: str, phone_number: str) -> Dict[str, Any]:
        """Unbind a cold-caller outbound number."""
        normalized_phone = normalize_phone_number(phone_number)
        phone = await self.get_phone_by_number(normalized_phone)
        if not phone or phone.get("tenant_id") != tenant_id:
            raise ValueError(f"Phone number {normalized_phone} not found for this tenant")

        if phone.get("usage_role") != COLD_CALLER_OUTBOUND_ROLE:
            raise ValueError(f"Phone number {normalized_phone} is not bound to Cold Caller outbound")

        updated = await store.update_phone_number(
            phone["id"],
            {
                "status": "inactive",
                "usage_role": VOICE_AGENT_INBOUND_ROLE,
                "role_status": ROLE_STATUS_INACTIVE,
                "conflict_code": None,
                "conflict_message": None,
            },
        )
        if not updated:
            raise ValueError("Failed to unbind outbound number")
        return self._normalize_phone_role_fields(updated)

    async def get_telephony_status(self, tenant_id: str) -> Dict[str, Any]:
        """Return tenant telephony status and conflicts."""
        numbers = await self.list_phones_by_tenant(tenant_id)
        integration = await store.get_twilio_integration(tenant_id)

        voice_count = len(
            [
                n
                for n in numbers
                if n.get("usage_role") == VOICE_AGENT_INBOUND_ROLE and n.get("role_status") == ROLE_STATUS_ACTIVE
            ]
        )
        cold_count = len(
            [
                n
                for n in numbers
                if n.get("usage_role") == COLD_CALLER_OUTBOUND_ROLE and n.get("role_status") == ROLE_STATUS_ACTIVE
            ]
        )
        conflicts = [n for n in numbers if n.get("role_status") == ROLE_STATUS_CONFLICT]

        return {
            "tenant_id": tenant_id,
            "has_shared_twilio_credentials": bool(integration),
            "shared_account_sid": integration.get("account_sid") if integration else None,
            "total_numbers": len(numbers),
            "voice_agent_inbound_numbers": voice_count,
            "cold_caller_outbound_numbers": cold_count,
            "conflicts_count": len(conflicts),
            "conflicts": conflicts,
        }


# Global phone number service instance
phone_number_service = PhoneNumberService()
