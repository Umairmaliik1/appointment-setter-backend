"""
Audit logging service for security-sensitive mutations.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from app.services.postgres_store import postgres_store

logger = logging.getLogger(__name__)


class AuditService:
    """Writes immutable audit events to PostgreSQL."""

    async def log_event(
        self,
        *,
        actor: Dict[str, Any],
        action: str,
        resource_type: str,
        resource_id: str,
        status: str = "success",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        actor_id = str(actor.get("id", ""))
        actor_email = str(actor.get("email", ""))
        actor_role = str(actor.get("role", ""))
        actor_org_id = str(actor.get("active_org_id") or "")

        payload = {
            "id": str(uuid.uuid4()),
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "status": status,
            "actor": {
                "user_id": actor_id,
                "email": actor_email,
                "role": actor_role,
                "active_org_id": actor_org_id,
            },
            "metadata": metadata or {},
        }
        try:
            return await postgres_store.create_audit_log(payload)
        except Exception as exc:
            logger.error("Failed to persist audit log event: %s", exc, exc_info=True)
            # Never break business flow because of audit storage issues.
            return payload


audit_service = AuditService()
