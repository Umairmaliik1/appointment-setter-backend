"""
Organization hierarchy and authorization helper service.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from app.core.config import (
    PLATFORM_BRAND_ACCENT_COLOR,
    PLATFORM_BRAND_LOGO_URL,
    PLATFORM_BRAND_NAME,
    PLATFORM_BRAND_PRIMARY_COLOR,
    PLATFORM_BRAND_SECONDARY_COLOR,
)
from app.services.postgres_store import postgres_store

PLATFORM_ORG_ID = "mindrind-platform"
PLATFORM_ROLES = {"platform_owner", "platform_staff", "admin"}
PARTNER_ROLES = {"partner_owner", "partner_admin", "partner_staff"}
CUSTOMER_ROLES = {"customer_owner", "customer_staff"}


@dataclass
class AccessContext:
    memberships: List[Dict[str, Any]]
    accessible_orgs: List[Dict[str, Any]]
    active_org: Optional[Dict[str, Any]]
    accessible_tenant_ids: List[str]
    platform_scope: bool


class OrgService:
    """Service for org tree, memberships, and access checks."""

    def _normalize_role(self, role: Optional[str]) -> str:
        return (role or "").strip().lower()

    def _org_sort_key(self, org: Dict[str, Any]) -> tuple:
        org_type = str(org.get("org_type", "")).lower()
        priority = {"platform": 0, "partner": 1, "customer": 2}.get(org_type, 9)
        return (priority, str(org.get("name", "")).lower(), str(org.get("id", "")))

    async def ensure_platform_org_exists(self) -> Dict[str, Any]:
        org = await postgres_store.get_org(PLATFORM_ORG_ID)
        if org:
            return org
        payload = {
            "id": PLATFORM_ORG_ID,
            "org_type": "platform",
            "parent_org_id": None,
            "name": PLATFORM_BRAND_NAME,
            "status": "active",
            "branding": {
                "brand_name": PLATFORM_BRAND_NAME,
                "logo_url": PLATFORM_BRAND_LOGO_URL or None,
                "primary_color": PLATFORM_BRAND_PRIMARY_COLOR,
                "secondary_color": PLATFORM_BRAND_SECONDARY_COLOR,
                "accent_color": PLATFORM_BRAND_ACCENT_COLOR,
            },
        }
        await postgres_store.create_org(payload)
        return payload

    async def _derive_legacy_memberships(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        role = self._normalize_role(user.get("role"))
        user_id = str(user.get("id", ""))
        derived: List[Dict[str, Any]] = []

        if role == "admin":
            await self.ensure_platform_org_exists()
            derived.append(
                {
                    "id": f"legacy:{user_id}:{PLATFORM_ORG_ID}",
                    "org_id": PLATFORM_ORG_ID,
                    "user_id": user_id,
                    "role": "platform_owner",
                    "status": "active",
                }
            )
            return derived

        tenant_id = user.get("tenant_id")
        if tenant_id:
            org = await postgres_store.get_org_by_legacy_tenant_id(str(tenant_id), prefer_customer=True)
            if org:
                mapped_role = "customer_staff"
                if role == "tenant_admin":
                    mapped_role = "customer_owner"
                derived.append(
                    {
                        "id": f"legacy:{user_id}:{org['id']}",
                        "org_id": org["id"],
                        "user_id": user_id,
                        "role": mapped_role,
                        "status": "active",
                    }
                )
        return derived

    async def get_user_memberships(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        user_id = str(user.get("id", ""))
        memberships = await postgres_store.list_org_memberships_for_user(user_id)
        active = [m for m in memberships if self._normalize_role(m.get("status")) != "inactive"]
        if active:
            return active
        return await self._derive_legacy_memberships(user)

    async def _resolve_accessible_orgs_from_memberships(self, memberships: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not memberships:
            return []

        org_ids: Set[str] = set()
        has_platform_scope = False
        for membership in memberships:
            org_id = str(membership.get("org_id", "")).strip()
            if not org_id:
                continue
            role = self._normalize_role(membership.get("role"))
            if role in PLATFORM_ROLES:
                has_platform_scope = True
            org_ids.add(org_id)

        if has_platform_scope:
            return await postgres_store.list_orgs(limit=10000, offset=0)

        for membership in memberships:
            org_id = str(membership.get("org_id", "")).strip()
            if not org_id:
                continue
            role = self._normalize_role(membership.get("role"))
            if role in PARTNER_ROLES:
                descendants = await postgres_store.list_descendant_orgs(org_id)
                org_ids.update([org["id"] for org in descendants])

        accessible: List[Dict[str, Any]] = []
        for org_id in org_ids:
            org = await postgres_store.get_org(org_id)
            if org:
                accessible.append(org)
        return sorted(accessible, key=self._org_sort_key)

    async def get_access_context(self, user: Dict[str, Any]) -> AccessContext:
        precomputed_memberships = user.get("org_memberships")
        precomputed_orgs = user.get("accessible_orgs")
        if isinstance(precomputed_memberships, list) and isinstance(precomputed_orgs, list):
            sorted_orgs = sorted(precomputed_orgs, key=self._org_sort_key)
            platform_scope = any(self._normalize_role(m.get("role")) in PLATFORM_ROLES for m in precomputed_memberships)
            active_org = None
            active_org_id = user.get("active_org_id")
            if platform_scope:
                active_org = next((org for org in sorted_orgs if str(org.get("id", "")) == PLATFORM_ORG_ID), None)
            if not active_org and isinstance(active_org_id, str):
                active_org = next((org for org in sorted_orgs if str(org.get("id", "")) == active_org_id), None)
            if not active_org and sorted_orgs:
                active_org = sorted_orgs[0]
            accessible_tenant_ids = sorted(
                {
                    str(org["legacy_tenant_id"])
                    for org in sorted_orgs
                    if org.get("legacy_tenant_id")
                    and str(org.get("org_type")) == "customer"
                }
            )
            return AccessContext(
                memberships=precomputed_memberships,
                accessible_orgs=sorted_orgs,
                active_org=active_org,
                accessible_tenant_ids=accessible_tenant_ids,
                platform_scope=platform_scope,
            )

        memberships = await self.get_user_memberships(user)
        accessible_orgs = await self._resolve_accessible_orgs_from_memberships(memberships)
        accessible_orgs = sorted(accessible_orgs, key=self._org_sort_key)
        accessible_map = {org["id"]: org for org in accessible_orgs}
        platform_scope = any(self._normalize_role(m.get("role")) in PLATFORM_ROLES for m in memberships)

        active_org_id = user.get("active_org_id")
        if platform_scope and PLATFORM_ORG_ID not in accessible_map:
            platform_org = await self.ensure_platform_org_exists()
            accessible_orgs.append(platform_org)
            accessible_orgs = sorted(accessible_orgs, key=self._org_sort_key)
            accessible_map = {org["id"]: org for org in accessible_orgs}

        active_org = None
        if platform_scope:
            active_org = accessible_map.get(PLATFORM_ORG_ID)
        if not active_org and isinstance(active_org_id, str):
            active_org = accessible_map.get(active_org_id)
        if not active_org and accessible_orgs:
            active_org = accessible_orgs[0]

        accessible_tenant_ids = sorted(
            {
                str(org["legacy_tenant_id"])
                for org in accessible_orgs
                if org.get("legacy_tenant_id")
                and str(org.get("org_type")) == "customer"
            }
        )

        return AccessContext(
            memberships=memberships,
            accessible_orgs=accessible_orgs,
            active_org=active_org,
            accessible_tenant_ids=accessible_tenant_ids,
            platform_scope=platform_scope,
        )

    async def user_can_access_org(self, user: Dict[str, Any], org_id: str) -> bool:
        access = await self.get_access_context(user)
        if access.platform_scope:
            return True
        return any(org.get("id") == org_id for org in access.accessible_orgs)

    async def user_can_access_legacy_tenant(self, user: Dict[str, Any], tenant_id: str) -> bool:
        access = await self.get_access_context(user)
        if access.platform_scope:
            return True
        return tenant_id in access.accessible_tenant_ids

    def is_platform_staff(self, memberships: List[Dict[str, Any]]) -> bool:
        return any(self._normalize_role(m.get("role")) in PLATFORM_ROLES for m in memberships)

    def is_partner_staff(self, memberships: List[Dict[str, Any]]) -> bool:
        return any(self._normalize_role(m.get("role")) in PARTNER_ROLES for m in memberships)

    async def get_partner_org_for_active_context(self, active_org: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not active_org:
            return None
        org_type = str(active_org.get("org_type", "")).lower()
        if org_type == "partner":
            return active_org
        if org_type == "customer":
            parent_org_id = active_org.get("parent_org_id")
            if isinstance(parent_org_id, str) and parent_org_id:
                return await postgres_store.get_org(parent_org_id)
        return None

    async def create_membership(
        self,
        org_id: str,
        user_id: str,
        role: str,
        status: str = "active",
    ) -> Dict[str, Any]:
        existing = await postgres_store.get_org_membership(org_id=org_id, user_id=user_id)
        payload = {
            "id": existing["id"] if existing else str(uuid.uuid4()),
            "org_id": org_id,
            "user_id": user_id,
            "role": self._normalize_role(role),
            "status": status,
        }
        await postgres_store.upsert_org_membership(payload)
        return payload


org_service = OrgService()
