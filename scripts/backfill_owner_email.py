"""
Backfill owner_email on tenants from business_configs.contact_email.

Usage:
  python scripts/backfill_owner_email.py
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.store import store

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

async def _build_user_email_index(limit: int) -> Tuple[Dict[str, str], Dict[str, str]]:
    offset = 0
    tenant_admin_emails: Dict[str, str] = {}
    tenant_user_emails: Dict[str, str] = {}

    while True:
        users = await store.list_users(limit=limit, offset=offset)
        if not users:
            break

        for user in users:
            tenant_id = user.get("tenant_id")
            email = (user.get("email") or "").strip()
            if not tenant_id or not email:
                continue

            role = (user.get("role") or "").lower()
            if tenant_id not in tenant_user_emails:
                tenant_user_emails[tenant_id] = email
            if role == "tenant_admin" and tenant_id not in tenant_admin_emails:
                tenant_admin_emails[tenant_id] = email

        offset += limit

    return tenant_admin_emails, tenant_user_emails


async def backfill(
    limit: int = 200,
    user_limit: int = 500,
    delete_timezone: bool = True,
    default_email: Optional[str] = None,
) -> int:
    tenant_admin_emails, tenant_user_emails = await _build_user_email_index(limit=user_limit)
    offset = 0
    updated_owner_email = 0
    updated_timezone = 0
    skipped = 0
    used_default = 0
    missing_owner_email = 0
    missing_tenant_id = 0
    default_email = (default_email or "").strip() or None

    while True:
        tenants = await store.list_tenants(limit=limit, offset=offset)
        if not tenants:
            break

        for tenant in tenants:
            tenant_id = tenant.get("id")
            if not tenant_id:
                missing_tenant_id += 1
                continue

            update_data = {}
            owner_email = (tenant.get("owner_email") or "").strip()

            if not owner_email:
                contact_email: Optional[str] = None
                try:
                    business_config = await store.get_business_config(tenant_id)
                except Exception as exc:
                    logger.warning("Failed to load business config for tenant %s: %s", tenant_id, exc)
                    business_config = None

                if business_config:
                    contact_email = (business_config.get("contact_email") or "").strip()

                if not contact_email:
                    contact_email = tenant_admin_emails.get(tenant_id) or tenant_user_emails.get(tenant_id)

                if contact_email:
                    update_data["owner_email"] = contact_email
                    updated_owner_email += 1
                elif default_email:
                    update_data["owner_email"] = default_email
                    updated_owner_email += 1
                    used_default += 1
                else:
                    missing_owner_email += 1
                    tenant_name = tenant.get("name", "unknown")
                    logger.warning(
                        "Missing owner_email for tenant %s (%s); no contact or user email found",
                        tenant_id,
                        tenant_name,
                    )

            if delete_timezone and "timezone" in tenant:
                update_data["timezone"] = None
                updated_timezone += 1

            if update_data:
                try:
                    await store.update_tenant(tenant_id, update_data)
                    logger.info("Updated tenant %s", tenant_id)
                except Exception as exc:
                    logger.warning("Failed to update tenant %s: %s", tenant_id, exc)
            else:
                skipped += 1

        offset += limit

    logger.info(
        "Backfill complete. owner_email=%s timezone_removed=%s used_default=%s skipped=%s missing_owner_email=%s missing_tenant_id=%s",
        updated_owner_email,
        updated_timezone,
        used_default,
        skipped,
        missing_owner_email,
        missing_tenant_id,
    )
    return updated_owner_email

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill tenant owner_email from business config contact_email."
    )
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--user-limit", type=int, default=500)
    parser.add_argument(
        "--keep-timezone",
        action="store_true",
        help="Do not delete the timezone field from tenants.",
    )
    parser.add_argument(
        "--default-email",
        help="Fallback email to use when no contact or user email exists.",
    )
    args = parser.parse_args()
    asyncio.run(
        backfill(
            limit=args.limit,
            user_limit=args.user_limit,
            delete_timezone=not args.keep_timezone,
            default_email=args.default_email,
        )
    )

if __name__ == "__main__":
    main()
