"""
Platform app catalog and access helpers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set

APPOINTMENT_SETTER_APP_ID = "appointment_setter"
CHATBOT_AGENTS_APP_ID = "chatbot_agents"
SMS_APP_ID = "sms"
USERS_APP_ID = "users"

PLATFORM_APPS: List[Dict[str, str]] = [
    {
        "id": APPOINTMENT_SETTER_APP_ID,
        "slug": "appointment-setter",
        "label": "Appointment Setter",
        "default_route": "/app/appointment-setter/dashboard",
        "icon_key": APPOINTMENT_SETTER_APP_ID,
        "description": "Calling and scheduling",
        "status": "active",
    },
    {
        "id": CHATBOT_AGENTS_APP_ID,
        "slug": "chatbot-agents",
        "label": "Chatbot Agents",
        "default_route": "/app/chatbot-agents",
        "icon_key": CHATBOT_AGENTS_APP_ID,
        "description": "Launchers and embeds",
        "status": "active",
    },
    {
        "id": SMS_APP_ID,
        "slug": "sms",
        "label": "SMS Outreach",
        "default_route": "/app/sms/dashboard",
        "icon_key": SMS_APP_ID,
        "description": "Cold-SMS campaigns and inbox",
        "status": "active",
    },
    {
        "id": USERS_APP_ID,
        "slug": "users",
        "label": "Users",
        "default_route": "/app/users",
        "icon_key": USERS_APP_ID,
        "description": "Staff, roles, and access",
        "status": "active",
    },
]

_PLATFORM_APP_IDS: Set[str] = {app["id"] for app in PLATFORM_APPS}


def get_platform_apps() -> List[Dict[str, str]]:
    """Return platform app catalog."""
    return [dict(app) for app in PLATFORM_APPS]


def get_platform_app_ids() -> List[str]:
    """Return valid app IDs."""
    return [app["id"] for app in PLATFORM_APPS]


def normalize_allowed_app_ids(app_ids: Optional[Sequence[str]]) -> List[str]:
    """Filter and deduplicate incoming app IDs while preserving order."""
    if not app_ids:
        return []

    normalized: List[str] = []
    seen: Set[str] = set()
    for app_id in app_ids:
        if not isinstance(app_id, str):
            continue
        cleaned = app_id.strip()
        if not cleaned or cleaned in seen or cleaned not in _PLATFORM_APP_IDS:
            continue
        normalized.append(cleaned)
        seen.add(cleaned)
    return normalized


def get_default_allowed_app_ids_for_role(role: str) -> List[str]:
    """Default app set for a role when explicit app access is missing."""
    role_name = (role or "").strip().lower()

    if role_name == "admin":
        return get_platform_app_ids()
    if role_name in {"tenant_admin", "tenant_user", "user"}:
        return [APPOINTMENT_SETTER_APP_ID, CHATBOT_AGENTS_APP_ID, SMS_APP_ID]

    return [APPOINTMENT_SETTER_APP_ID]


def resolve_user_allowed_app_ids(user: Optional[Dict[str, Any]]) -> List[str]:
    """
    Resolve effective app access for a user.

    Priority:
    1) explicit user.allowed_app_ids
    2) role defaults
    """
    if not user:
        return []
    role = str(user.get("role") or "").lower()
    explicit = normalize_allowed_app_ids(user.get("allowed_app_ids"))
    if explicit:
        # Backward-compatible rollout: older users were often saved with only
        # appointment_setter explicitly, which unintentionally blocks chatbot_agents.
        if role in {"tenant_admin", "tenant_user", "user"} and CHATBOT_AGENTS_APP_ID not in explicit:
            explicit = [*explicit, CHATBOT_AGENTS_APP_ID]
        return explicit
    return get_default_allowed_app_ids_for_role(role)


def resolve_user_default_app_id(user: Optional[Dict[str, Any]], allowed_app_ids: Sequence[str]) -> str:
    """Resolve the user's default app from profile and allowed list."""
    preferred = user.get("default_app_id") if user else None
    if isinstance(preferred, str) and preferred in allowed_app_ids:
        return preferred

    if allowed_app_ids:
        return allowed_app_ids[0]

    return APPOINTMENT_SETTER_APP_ID


def has_app_access(user: Optional[Dict[str, Any]], app_id: str) -> bool:
    """Check app access for user."""
    if not user:
        return False
    if str(user.get("role") or "").lower() == "admin":
        return app_id in _PLATFORM_APP_IDS
    return app_id in resolve_user_allowed_app_ids(user)


# Backward-compatible aliases for older imports/callers.
PLATFORM_APP_CATALOG = PLATFORM_APPS
ALL_PLATFORM_APP_IDS = get_platform_app_ids()


def sanitize_app_ids(app_ids: Optional[Sequence[str]]) -> List[str]:
    return normalize_allowed_app_ids(app_ids)


def get_platform_app_catalog() -> List[Dict[str, str]]:
    return get_platform_apps()


def resolve_allowed_app_ids(user: Optional[Dict[str, Any]]) -> List[str]:
    return resolve_user_allowed_app_ids(user)


def resolve_default_app_id(user: Optional[Dict[str, Any]]) -> Optional[str]:
    allowed_app_ids = resolve_user_allowed_app_ids(user)
    if not allowed_app_ids:
        return None
    return resolve_user_default_app_id(user, allowed_app_ids)
