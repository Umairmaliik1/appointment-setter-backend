"""
Populate business_configs.contact_email for tenants.

Usage:
  python scripts/populate_business_contact_email.py
"""

import argparse
import asyncio
import logging
import sys
import uuid
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

            if tenant_id not in tenant_user_emails:
                tenant_user_emails[tenant_id] = email

            role = (user.get("role") or "").lower()
            if role == "tenant_admin" and tenant_id not in tenant_admin_emails:
                tenant_admin_emails[tenant_id] = email

        offset += limit

    return tenant_admin_emails, tenant_user_emails


async def populate(limit: int = 200, user_limit: int = 500, default_email: Optional[str] = None) -> int:
    tenant_admin_emails, tenant_user_emails = await _build_user_email_index(limit=user_limit)
    default_email = (default_email or "").strip() or None

    offset = 0
    updated = 0
    created = 0
    skipped = 0
    missing_email = 0
    missing_tenant_id = 0

    while True:
        tenants = await store.list_tenants(limit=limit, offset=offset)
        if not tenants:
            break

        for tenant in tenants:
            tenant_id = tenant.get("id")
            if not tenant_id:
                missing_tenant_id += 1
                continue

            try:
                business_config = await store.get_business_config(tenant_id)
            except Exception as exc:
                logger.warning("Failed to load business config for tenant %s: %s", tenant_id, exc)
                business_config = None

            existing_email = ""
            if business_config:
                existing_email = (business_config.get("contact_email") or "").strip()

            if existing_email:
                skipped += 1
                continue

            owner_email = (tenant.get("owner_email") or "").strip()
            chosen_email = (
                owner_email
                or tenant_admin_emails.get(tenant_id)
                or tenant_user_emails.get(tenant_id)
                or default_email
            )

            if not chosen_email:
                missing_email += 1
                tenant_name = tenant.get("name", "unknown")
                logger.warning("No email found for tenant %s (%s)", tenant_id, tenant_name)
                continue

            if business_config:
                try:
                    await store.update_business_config(tenant_id, {"contact_email": chosen_email})
                    updated += 1
                    logger.info("Updated business contact_email for tenant %s", tenant_id)
                except Exception as exc:
                    logger.warning("Failed to update business config for tenant %s: %s", tenant_id, exc)
            else:
                business_dict = {
                    "id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "business_name": tenant.get("name") or "Business",
                    "contact_email": chosen_email,
                }
                try:
                    await store.create_business_config(business_dict)
                    created += 1
                    logger.info("Created business config for tenant %s", tenant_id)
                except Exception as exc:
                    logger.warning("Failed to create business config for tenant %s: %s", tenant_id, exc)

        offset += limit

    logger.info(
        "Populate complete. updated=%s created=%s skipped=%s missing_email=%s missing_tenant_id=%s",
        updated,
        created,
        skipped,
        missing_email,
        missing_tenant_id,
    )
    return updated + created


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Populate business_configs.contact_email for tenants."
    )
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--user-limit", type=int, default=500)
    parser.add_argument(
        "--default-email",
        help="Fallback email to use when no owner or user email exists.",
    )
    args = parser.parse_args()
    asyncio.run(populate(limit=args.limit, user_limit=args.user_limit, default_email=args.default_email))


if __name__ == "__main__":
    main()
