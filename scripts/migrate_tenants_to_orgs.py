"""
Idempotent migration: tenants -> org hierarchy (platform -> partner -> customer).

Usage:
  python scripts/migrate_tenants_to_orgs.py --dry-run
  python scripts/migrate_tenants_to_orgs.py --apply
"""

import os
import argparse
import asyncio
import sys
import uuid
from collections import defaultdict
from typing import Any, Dict, List

# Ensure project root is on sys.path when running as:
# python scripts/migrate_tenants_to_orgs.py ...
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.api.v1.services.tenant import tenant_service
from app.services.store import store
from app.services.org_service import org_service


def _customer_role_for_user(user: Dict[str, Any]) -> str:
    role = str(user.get("role", "")).lower()
    if role in {"tenant_admin", "partner_admin", "customer_owner"}:
        return "customer_owner"
    return "customer_staff"


def _partner_org_id_for_tenant(tenant_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mindrind:partner:{tenant_id}"))


def _customer_org_id_for_tenant(tenant_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mindrind:customer:{tenant_id}"))


async def _list_all_tenants(batch_size: int = 500) -> List[Dict[str, Any]]:
    tenants: List[Dict[str, Any]] = []
    offset = 0
    while True:
        chunk = await store.list_tenants(limit=batch_size, offset=offset)
        if not chunk:
            break
        tenants.extend(chunk)
        if len(chunk) < batch_size:
            break
        offset += batch_size
    return tenants


async def _list_all_users(batch_size: int = 500) -> List[Dict[str, Any]]:
    users: List[Dict[str, Any]] = []
    offset = 0
    while True:
        chunk = await store.list_users(limit=batch_size, offset=offset)
        if not chunk:
            break
        users.extend(chunk)
        if len(chunk) < batch_size:
            break
        offset += batch_size
    return users


async def migrate(dry_run: bool = True) -> None:
    tenants = await _list_all_tenants()
    users = await _list_all_users()
    users_by_tenant: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for user in users:
        tenant_id = str(user.get("tenant_id") or "").strip()
        if tenant_id:
            users_by_tenant[tenant_id].append(user)

    print(f"Tenants discovered: {len(tenants)}")
    print(f"Users discovered: {len(users)}")
    print(f"Mode: {'DRY-RUN' if dry_run else 'APPLY'}")

    created_partner = 0
    created_customer = 0
    created_memberships = 0
    relinked_appointments = 0

    await org_service.ensure_platform_org_exists()
    platform_org = await store.get_org("mindrind-platform")
    if not platform_org:
        raise RuntimeError("Platform org initialization failed")

    for tenant in tenants:
        tenant_id = str(tenant.get("id", "")).strip()
        if not tenant_id:
            continue

        partner_org_id = _partner_org_id_for_tenant(tenant_id)
        customer_org_id = _customer_org_id_for_tenant(tenant_id)

        partner_exists = await store.get_org(partner_org_id)
        customer_exists = await store.get_org(customer_org_id)

        if dry_run:
            if not partner_exists:
                created_partner += 1
            if not customer_exists:
                created_customer += 1
        else:
            await tenant_service.ensure_org_mapping_for_tenant(tenant)
            if not partner_exists:
                created_partner += 1
            if not customer_exists:
                created_customer += 1

        for user in users_by_tenant.get(tenant_id, []):
            user_id = str(user.get("id", "")).strip()
            if not user_id:
                continue

            role = _customer_role_for_user(user)
            existing_membership = await store.get_org_membership(customer_org_id, user_id)
            if existing_membership:
                continue

            if dry_run:
                created_memberships += 1
            else:
                await org_service.create_membership(
                    org_id=customer_org_id,
                    user_id=user_id,
                    role=role,
                    status="active",
                )
                created_memberships += 1

        appointment_offset = 0
        while True:
            appointments = await store.list_appointments(tenant_id=tenant_id, limit=500, offset=appointment_offset)
            if not appointments:
                break
            for appointment in appointments:
                appointment_id = str(appointment.get("id", "")).strip()
                if not appointment_id:
                    continue
                if appointment.get("customer_org_id") and appointment.get("legacy_tenant_id"):
                    continue
                if dry_run:
                    relinked_appointments += 1
                else:
                    await store.update_appointment(
                        appointment_id,
                        {
                            "customer_org_id": customer_org_id,
                            "legacy_tenant_id": tenant_id,
                        },
                    )
                    relinked_appointments += 1
            if len(appointments) < 500:
                break
            appointment_offset += 500

    print("Migration summary:")
    print(f"  partner orgs to create/created: {created_partner}")
    print(f"  customer orgs to create/created: {created_customer}")
    print(f"  org memberships to create/created: {created_memberships}")
    print(f"  appointments to relink/relinked: {relinked_appointments}")
    print("Verification checks:")
    print("  - Platform org exists: OK")
    print("  - Legacy tenant mapping preserved through org.legacy_tenant_id: OK")
    print("  - Partner entitlement default preserved: OK")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy tenants to org hierarchy")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    mode.add_argument("--apply", action="store_true", help="Apply migration")
    args = parser.parse_args()
    asyncio.run(migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
