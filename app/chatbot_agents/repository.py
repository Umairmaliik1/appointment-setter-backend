"""
Repository for chatbot agent persistence.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.store import store


class ChatbotAgentRepository:
    """PostgreSQL repository for chatbot agents."""

    async def create(self, chatbot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a chatbot agent record."""
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "id": str(uuid.uuid4()),
            "agent_type": "chatbot",
            "created_at": now,
            "updated_at": now,
            **chatbot_data,
        }
        return await store.create_chatbot_agent(payload)

    async def get(self, chatbot_id: str) -> Optional[Dict[str, Any]]:
        """Fetch chatbot by id."""
        return await store.get_chatbot_agent(chatbot_id)

    async def list_for_owner(self, owner_user_id: str, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """List chatbots for a specific owner."""
        return await store.list_chatbot_agents_by_owner(owner_user_id, limit=limit, offset=offset)

    async def list_all(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """List all chatbots."""
        return await store.list_chatbot_agents(limit=limit, offset=offset)

    async def update(self, chatbot_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update chatbot by id."""
        payload = {**update_data, "updated_at": datetime.now(timezone.utc).isoformat()}
        return await store.update_chatbot_agent(chatbot_id, payload)

    async def delete(self, chatbot_id: str) -> bool:
        """Delete chatbot by id."""
        return await store.delete_chatbot_agent(chatbot_id)

    async def create_runtime_log(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        """Persist chatbot runtime log entry."""
        return await store.create_chatbot_runtime_log(log_data)

    async def list_runtime_logs(
        self, chatbot_id: str, limit: int = 100, status_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List runtime log entries by chatbot id."""
        return await store.list_chatbot_runtime_logs(chatbot_id, limit=limit, status_filter=status_filter)

    async def get_runtime_settings(self) -> Dict[str, Any]:
        """Get global chatbot runtime settings."""
        return await store.get_chatbot_runtime_settings()

    async def update_runtime_settings(self, enabled: bool, updated_by: str) -> Dict[str, Any]:
        """Update global chatbot runtime settings."""
        return await store.update_chatbot_runtime_settings(enabled=enabled, updated_by=updated_by)

    async def create_chat_session(self, session_data: Dict[str, Any]) -> Dict[str, Any]:
        return await store.create_chatbot_chat_session(session_data)

    async def get_chat_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return await store.get_chatbot_chat_session(session_id)

    async def update_chat_session(self, session_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        payload = {**update_data, "updated_at": datetime.now(timezone.utc).isoformat()}
        return await store.update_chatbot_chat_session(session_id, payload)

    async def find_open_chat_session(self, chatbot_id: str, visitor_session_id: str, origin: str) -> Optional[Dict[str, Any]]:
        return await store.find_open_chatbot_chat_session(chatbot_id, visitor_session_id, origin)

    async def list_chat_sessions_for_owner(self, owner_user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        return await store.list_chatbot_chat_sessions_by_owner(owner_user_id, limit=limit)

    async def list_chat_sessions(self, limit: int = 100) -> List[Dict[str, Any]]:
        return await store.list_chatbot_chat_sessions(limit=limit)

    async def count_chat_sessions_for_visitor(self, chatbot_id: str, visitor_session_id: str, origin: str) -> int:
        return await store.count_chatbot_chat_sessions_for_visitor(chatbot_id, visitor_session_id, origin)

    async def create_chat_message(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        return await store.create_chatbot_chat_message(message_data)

    async def list_chat_messages(self, session_id: str, limit: int = 250) -> List[Dict[str, Any]]:
        return await store.list_chatbot_chat_messages(session_id, limit=limit)


chatbot_agent_repository = ChatbotAgentRepository()
