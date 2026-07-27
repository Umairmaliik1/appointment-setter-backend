"""
Authentication models and enums.
"""

from enum import Enum


class UserRole(str, Enum):
    """User roles in the system."""

    ADMIN = "admin"
    TENANT_ADMIN = "tenant_admin"
    TENANT_USER = "tenant_user"
    USER = "user"


class UserStatus(str, Enum):
    """User status in the system."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING = "pending"
