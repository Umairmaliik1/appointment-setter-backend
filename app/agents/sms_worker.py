"""SMS drip worker.

A standalone async process that polls the ``scheduled_sends`` outbox, claims due
rows with ``SELECT ... FOR UPDATE SKIP LOCKED`` (so multiple worker replicas never
double-send), enforces a per-tenant per-minute throttle via Redis, and dispatches
each message through the tenant's Twilio credentials.

Run with: ``python run_sms_worker.py start`` (see repo-root launcher), or
``python -m app.agents.sms_worker``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from app.core import config
from app.core.async_redis import async_redis_client
from app.services.store import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [sms-worker] %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sms_worker")


async def _throttle_ok(tenant_id: str, limit_per_min: int) -> bool:
    """Per-tenant per-minute send budget using a Redis minute-bucket counter."""
    try:
        client = await async_redis_client.get_client()
        minute = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        key = f"sms_throttle:{tenant_id}:{minute}"
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, 120)
        return count <= max(1, limit_per_min)
    except Exception as exc:  # pragma: no cover - if Redis is down, don't hard-block sends
        logger.warning("Throttle check failed (allowing send): %s", exc)
        return True


async def _dispatch(send: Dict[str, Any]) -> None:
    """Send one claimed row, with race-safe re-checks, then advance/retry."""
    # Import here to avoid import cycle (services import store; worker imports services).
    from app.api.v1.services.sms import sms_send_service

    send_id = send["id"]
    tenant_id = send["tenant_id"]
    lead_id = send.get("lead_id")
    to_number = send["to_phone_number"]
    body = send.get("body") or ""
    from_number = send.get("from_phone_number")
    campaign_id = send.get("campaign_id")

    # Race-safe re-checks (a reply/opt-out may have landed after this row was scheduled).
    if await store.is_sms_suppressed(tenant_id, to_number):
        await store.update_scheduled_send(send_id, {"status": "suppressed"})
        return

    enrollment = await store.get_sms_enrollment(send["enrollment_id"])
    if enrollment and enrollment.get("state") in {"paused_on_reply", "opted_out", "completed", "failed"}:
        await store.update_scheduled_send(send_id, {"status": "canceled"})
        return

    # Resolve effective throttle from the campaign.
    limit_per_min = config.SMS_DEFAULT_THROTTLE_PER_MIN
    campaign = await store.get_sms_campaign(campaign_id) if campaign_id else None
    if campaign and campaign.get("throttle_per_min"):
        limit_per_min = int(campaign["throttle_per_min"])

    if not await _throttle_ok(tenant_id, limit_per_min):
        # Defer ~1 minute; put the row back to scheduled.
        await store.update_scheduled_send(
            send_id,
            {"status": "scheduled", "send_after": datetime.now(timezone.utc) + timedelta(seconds=60)},
        )
        return

    try:
        await sms_send_service.send(
            tenant_id=tenant_id,
            to_number=to_number,
            from_number=from_number,
            body=body,
            campaign_id=campaign_id,
            lead_id=lead_id,
        )
        await store.update_scheduled_send(send_id, {"status": "sent"})
        if enrollment:
            await store.update_sms_enrollment(
                enrollment["id"], {"current_step": int(send.get("step_index") or 0) + 1}
            )
    except Exception as exc:
        attempts = int(send.get("attempts") or 0) + 1
        if attempts >= config.SMS_MAX_SEND_ATTEMPTS:
            logger.error("Send %s failed permanently after %d attempts: %s", send_id, attempts, exc)
            await store.update_scheduled_send(send_id, {"status": "failed", "attempts": attempts, "last_error": str(exc)})
        else:
            backoff = 2 ** attempts * 30  # 60s, 120s, ...
            logger.warning("Send %s failed (attempt %d), retrying in %ds: %s", send_id, attempts, backoff, exc)
            await store.update_scheduled_send(
                send_id,
                {
                    "status": "scheduled",
                    "attempts": attempts,
                    "last_error": str(exc),
                    "send_after": datetime.now(timezone.utc) + timedelta(seconds=backoff),
                },
            )


async def _wait_for_schema(max_wait_seconds: float = 120.0) -> None:
    """Block until the DB is reachable and the scheduled_sends table exists.

    On a fresh deploy the migration runs in a separate one-off container, so the
    worker can briefly come up before ``alembic upgrade head`` finishes. Rather
    than crash-loop on a missing relation, wait (with backoff) for the schema to
    appear. Bounded so a genuinely broken DB still surfaces in logs.
    """
    from sqlalchemy import text

    from app.services.database import get_engine

    waited = 0.0
    delay = 2.0
    while True:
        try:
            with get_engine().connect() as conn:
                # to_regclass returns NULL when the table doesn't exist yet.
                exists = conn.execute(text("SELECT to_regclass('public.scheduled_sends')")).scalar()
            if exists:
                if waited:
                    logger.info("scheduled_sends table is ready after %.0fs", waited)
                return
            logger.warning("Waiting for scheduled_sends table (migrations may still be running)...")
        except Exception as exc:
            logger.warning("Waiting for database to become ready: %s", exc)
        if waited >= max_wait_seconds:
            logger.error(
                "scheduled_sends not available after %.0fs; continuing anyway (loop will retry).",
                max_wait_seconds,
            )
            return
        await asyncio.sleep(delay)
        waited += delay
        delay = min(delay * 1.5, 10.0)


async def run_once() -> int:
    """Claim and dispatch one batch of due sends. Returns the number processed."""
    claimed = await store.claim_due_scheduled_sends(limit=config.SMS_WORKER_BATCH_SIZE)
    for send in claimed:
        try:
            await _dispatch(send)
        except Exception as exc:  # pragma: no cover - a single row must not kill the loop
            logger.error("Unexpected error dispatching send %s: %s", send.get("id"), exc, exc_info=True)
    return len(claimed)


async def run_forever() -> None:
    logger.info(
        "SMS worker starting (batch=%d, poll=%.1fs, default_throttle=%d/min)",
        config.SMS_WORKER_BATCH_SIZE,
        config.SMS_WORKER_POLL_INTERVAL_SECONDS,
        config.SMS_DEFAULT_THROTTLE_PER_MIN,
    )
    # Survive the deploy race where the worker boots before migrations finish.
    await _wait_for_schema()
    while True:
        try:
            processed = await run_once()
        except Exception as exc:  # pragma: no cover - keep the loop alive on transient DB errors
            logger.error("SMS worker poll failed: %s", exc, exc_info=True)
            processed = 0
        # Sleep only when idle so bursts drain quickly.
        if processed == 0:
            await asyncio.sleep(config.SMS_WORKER_POLL_INTERVAL_SECONDS)


def main() -> None:
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        logger.info("SMS worker stopped")


if __name__ == "__main__":
    main()
