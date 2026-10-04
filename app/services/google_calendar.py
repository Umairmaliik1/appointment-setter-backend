"""Google Calendar service for Google Calendar Sync (v1 spec).

Handles:
- OAuth token refresh with invalid_grant detection.
- FreeBusy queries with Redis caching (60s).
- Event creation with deterministic idempotent event IDs.
- Event updates (reschedule) and deletions (cancel) with 404/410 handling.
- Background sync with exponential backoff retries and graceful fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from app.core.async_redis import async_redis_client
from app.core.config import (
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
)
from app.core.datetime_utils import parse_to_utc_datetime
from app.core.encryption import encryption_service
from app.services.email.service import EmailService
from app.services.store import store

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
DEFAULT_TIMEOUT_SECONDS = 5.0


def generate_google_event_id(appointment_id: str) -> str:
    """Generate a valid Google Calendar event ID.

    Google Calendar requirements:
    - Allowed characters: characters a-v and 0-9.
    - Length: between 5 and 1024 characters.
    UUIDs stripped of dashes are 32 hexadecimal chars [0-9a-f],
    which are a valid subset of [a-v0-9].
    Fallback to sha1 hex digest if non-conforming characters are present.
    """
    clean_id = str(appointment_id).lower().replace("-", "")
    if re.fullmatch(r"[a-v0-9]{5,1024}", clean_id):
        return clean_id
    return hashlib.sha1(str(appointment_id).encode("utf-8")).hexdigest()


class GoogleCalendarService:
    """Service for interacting with Google Calendar API."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._client = client
        self.redis_client = async_redis_client
        self.email_service = EmailService()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS)

    async def get_access_token(self, tenant_id: str) -> Optional[str]:
        """Retrieve a valid Google access token for the tenant, refreshing if needed."""
        connection = await store.get_calendar_connection(tenant_id)
        if not connection:
            logger.debug("No calendar connection found for tenant %s", tenant_id)
            return None

        if connection.get("status") in ("needs_reauth", "revoked"):
            logger.warning(
                "Calendar connection for tenant %s is in state '%s'",
                tenant_id,
                connection.get("status"),
            )
            return None

        # Check Redis cache for cached access token
        cache_key = f"gcal_access_token:{tenant_id}"
        cached_token = await self.redis_client.get(cache_key)
        if cached_token:
            return cached_token

        # Decrypt refresh token
        encrypted_token = connection.get("refresh_token_enc")
        if not encrypted_token:
            logger.error("No refresh token stored for tenant %s", tenant_id)
            return None

        try:
            refresh_token = encryption_service.decrypt(encrypted_token)
        except Exception as exc:
            logger.error("Failed to decrypt refresh token for tenant %s: %s", tenant_id, exc)
            return None

        # Request new access token from Google
        payload = {
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

        try:
            async with self._get_client() as client:
                response = await client.post(GOOGLE_TOKEN_URL, data=payload)

            if response.status_code == 200:
                token_data = response.json()
                access_token = token_data.get("access_token")
                expires_in = token_data.get("expires_in", 3600)
                # Cache token, expiring slightly before Google's TTL
                ttl = max(60, int(expires_in) - 120)
                await self.redis_client.set(cache_key, access_token, ttl=ttl)
                return access_token

            # Check for invalid_grant (revocation or expiration of refresh token)
            try:
                error_body = response.json()
            except Exception:
                error_body = {}

            error_type = error_body.get("error", "")
            logger.error(
                "Google token refresh failed for tenant %s: HTTP %s - %s",
                tenant_id,
                response.status_code,
                error_body,
            )

            if response.status_code == 400 and error_type == "invalid_grant":
                logger.warning(
                    "Invalid grant for tenant %s. Marking status as needs_reauth.",
                    tenant_id,
                )
                await store.update_calendar_connection_status(tenant_id, "needs_reauth")
                await self._notify_admin_reauth_needed(tenant_id)

            return None

        except Exception as exc:
            logger.exception("Exception refreshing Google token for tenant %s: %s", tenant_id, exc)
            return None

    async def free_busy(
        self,
        tenant_id: str,
        start_dt: datetime,
        end_dt: datetime,
        skip_cache: bool = False,
    ) -> List[Tuple[datetime, datetime]]:
        """Query Google Calendar FreeBusy API for busy intervals in the given range.

        Returns list of (start, end) datetime pairs in UTC.
        Cached in Redis for 60 seconds (bypassable via skip_cache=True for fresh booking validation).
        """
        connection = await store.get_calendar_connection(tenant_id)
        if not connection or connection.get("status") != "active":
            return []

        # Ensure start_dt and end_dt are timezone-aware
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)

        cache_key = f"gcal_freebusy:{tenant_id}:{start_dt.isoformat()}:{end_dt.isoformat()}"
        if not skip_cache:
            cached_result = await self.redis_client.get(cache_key)
            if cached_result:
                try:
                    raw_intervals = json.loads(cached_result)
                    return [
                        (
                            datetime.fromisoformat(item[0]).astimezone(timezone.utc),
                            datetime.fromisoformat(item[1]).astimezone(timezone.utc),
                        )
                        for item in raw_intervals
                    ]
                except Exception:
                    pass

        access_token = await self.get_access_token(tenant_id)
        if not access_token:
            return []

        calendar_id = connection.get("calendar_id", "primary")
        tenant_tz = connection.get("timezone", "UTC")

        body = {
            "timeMin": start_dt.isoformat(),
            "timeMax": end_dt.isoformat(),
            "timeZone": tenant_tz,
            "items": [{"id": calendar_id}],
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with self._get_client() as client:
                response = await client.post(
                    f"{GOOGLE_CALENDAR_API_BASE}/freeBusy",
                    json=body,
                    headers=headers,
                )
            if response.status_code != 200:
                logger.warning(
                    "Google freeBusy query failed for tenant %s: HTTP %s - %s",
                    tenant_id,
                    response.status_code,
                    response.text,
                )
                return []

            data = response.json()
            cal_data = data.get("calendars", {}).get(calendar_id, {})
            busy_entries = cal_data.get("busy", [])

            busy_intervals: List[Tuple[datetime, datetime]] = []
            for entry in busy_entries:
                try:
                    b_start = datetime.fromisoformat(entry["start"]).astimezone(timezone.utc)
                    b_end = datetime.fromisoformat(entry["end"]).astimezone(timezone.utc)
                    busy_intervals.append((b_start, b_end))
                except Exception as parse_err:
                    logger.warning("Error parsing busy interval %s: %s", entry, parse_err)

            # Cache in Redis for 60 seconds
            cache_payload = [
                (b[0].isoformat(), b[1].isoformat()) for b in busy_intervals
            ]
            await self.redis_client.set(cache_key, json.dumps(cache_payload), ttl=60)
            return busy_intervals

        except Exception as exc:
            logger.warning("Error querying Google freeBusy for tenant %s: %s", tenant_id, exc)
            return []

    async def create_event(self, tenant_id: str, appointment: Dict[str, Any]) -> Optional[str]:
        """Create a Google Calendar event for an appointment.

        Uses deterministic event ID for idempotency:
        - 409 Conflict is treated as success.
        Returns Google event ID on success, or None on failure.
        """
        connection = await store.get_calendar_connection(tenant_id)
        if not connection or connection.get("status") != "active":
            return None

        access_token = await self.get_access_token(tenant_id)
        if not access_token:
            return None

        calendar_id = connection.get("calendar_id", "primary")
        tenant_tz = connection.get("timezone", "UTC")

        # Parse appointment start datetime
        raw_datetime = appointment.get("appointment_datetime")
        start_dt = parse_to_utc_datetime(raw_datetime) if raw_datetime else None
        if not start_dt:
            logger.error("Cannot create calendar event: invalid appointment datetime %s", raw_datetime)
            return None

        duration_minutes = appointment.get("duration_minutes") or 60
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        # Format localized start & end
        try:
            target_tz = ZoneInfo(tenant_tz)
            start_local = start_dt.astimezone(target_tz)
            end_local = end_dt.astimezone(target_tz)
        except Exception:
            start_local = start_dt
            end_local = end_dt

        event_id = generate_google_event_id(appointment["id"])

        service_type = appointment.get("service_type") or "Appointment"
        customer_name = appointment.get("customer_name") or "Customer"
        summary = f"{service_type} - {customer_name}"
        location = appointment.get("service_address") or ""
        customer_phone = appointment.get("customer_phone") or "Not provided"
        customer_email = appointment.get("customer_email") or "Not provided"
        service_details = appointment.get("service_details") or "None"

        description = (
            f"Phone: {customer_phone}\n"
            f"Email: {customer_email}\n"
            f"Details: {service_details}\n\n"
            "Booked by ShipStack Voice"
        )

        event_payload = {
            "id": event_id,
            "summary": summary,
            "location": location,
            "description": description,
            "start": {
                "dateTime": start_local.isoformat(),
                "timeZone": tenant_tz,
            },
            "end": {
                "dateTime": end_local.isoformat(),
                "timeZone": tenant_tz,
            },
        }

        url = f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events?sendUpdates=none"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with self._get_client() as client:
                response = await client.post(url, json=event_payload, headers=headers)

            if response.status_code in (200, 201):
                created = response.json()
                g_id = created.get("id", event_id)
                await self._mark_appointment_synced(appointment["id"], g_id, tenant_id)
                return g_id

            if response.status_code == 409:
                # Event already exists (idempotency) - treat as success
                logger.info(
                    "Google event %s already exists (409 Conflict). Treating as success.",
                    event_id,
                )
                await self._mark_appointment_synced(appointment["id"], event_id, tenant_id)
                return event_id

            logger.error(
                "Failed to create Google event for appointment %s: HTTP %s - %s",
                appointment["id"],
                response.status_code,
                response.text,
            )
            return None

        except Exception as exc:
            logger.exception(
                "Exception creating Google Calendar event for appointment %s: %s",
                appointment.get("id"),
                exc,
            )
            return None

    async def update_event(
        self, tenant_id: str, event_id: str, appointment: Dict[str, Any]
    ) -> bool:
        """Update an existing Google Calendar event (e.g. reschedule)."""
        connection = await store.get_calendar_connection(tenant_id)
        if not connection or connection.get("status") != "active":
            return False

        access_token = await self.get_access_token(tenant_id)
        if not access_token:
            return False

        calendar_id = connection.get("calendar_id", "primary")
        tenant_tz = connection.get("timezone", "UTC")

        raw_datetime = appointment.get("appointment_datetime")
        start_dt = parse_to_utc_datetime(raw_datetime) if raw_datetime else None
        if not start_dt:
            return False

        duration_minutes = appointment.get("duration_minutes") or 60
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        try:
            target_tz = ZoneInfo(tenant_tz)
            start_local = start_dt.astimezone(target_tz)
            end_local = end_dt.astimezone(target_tz)
        except Exception:
            start_local = start_dt
            end_local = end_dt

        service_type = appointment.get("service_type") or "Appointment"
        customer_name = appointment.get("customer_name") or "Customer"
        summary = f"{service_type} - {customer_name}"
        location = appointment.get("service_address") or ""
        customer_phone = appointment.get("customer_phone") or "Not provided"
        customer_email = appointment.get("customer_email") or "Not provided"
        service_details = appointment.get("service_details") or "None"

        patch_payload = {
            "summary": summary,
            "location": location,
            "description": (
                f"Phone: {customer_phone}\n"
                f"Email: {customer_email}\n"
                f"Details: {service_details}\n\n"
                "Booked by ShipStack Voice"
            ),
            "start": {
                "dateTime": start_local.isoformat(),
                "timeZone": tenant_tz,
            },
            "end": {
                "dateTime": end_local.isoformat(),
                "timeZone": tenant_tz,
            },
        }

        url = f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}?sendUpdates=none"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with self._get_client() as client:
                response = await client.patch(url, json=patch_payload, headers=headers)

            if response.status_code in (200, 204):
                await self._mark_appointment_synced(appointment["id"], event_id, tenant_id)
                return True

            logger.warning(
                "Failed to patch Google event %s: HTTP %s - %s",
                event_id,
                response.status_code,
                response.text,
            )
            return False

        except Exception as exc:
            logger.exception("Exception updating Google event %s: %s", event_id, exc)
            return False

    async def delete_event(self, tenant_id: str, event_id: str) -> bool:
        """Delete a Google Calendar event. Treats 404 and 410 as success (already gone)."""
        connection = await store.get_calendar_connection(tenant_id)
        if not connection:
            return True

        access_token = await self.get_access_token(tenant_id)
        if not access_token:
            return True

        calendar_id = connection.get("calendar_id", "primary")
        url = f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}?sendUpdates=none"
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            async with self._get_client() as client:
                response = await client.delete(url, headers=headers)

            if response.status_code in (200, 204, 404, 410):
                logger.info(
                    "Google Calendar event %s deleted successfully (status %s)",
                    event_id,
                    response.status_code,
                )
                return True

            logger.warning(
                "Google Calendar event delete for %s returned HTTP %s: %s",
                event_id,
                response.status_code,
                response.text,
            )
            return False

        except Exception as exc:
            logger.exception("Exception deleting Google event %s: %s", event_id, exc)
            return False

    async def revoke_connection(self, tenant_id: str) -> bool:
        """Revoke OAuth token at Google and delete calendar connection from database."""
        connection = await store.get_calendar_connection(tenant_id)
        if not connection:
            return True

        encrypted_token = connection.get("refresh_token_enc")
        if encrypted_token:
            try:
                refresh_token = encryption_service.decrypt(encrypted_token)
                async with self._get_client() as client:
                    await client.post(
                        GOOGLE_REVOKE_URL,
                        params={"token": refresh_token},
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
            except Exception as exc:
                logger.warning("Revoke token call at Google failed (non-fatal): %s", exc)

        # Clear Redis caches
        await self.redis_client.delete(f"gcal_access_token:{tenant_id}")

        # Delete database row
        await store.delete_calendar_connection(tenant_id)
        return True

    async def sync_appointment_event_with_retry(
        self, tenant_id: str, appointment: Dict[str, Any], max_attempts: int = 3
    ) -> Optional[str]:
        """Create Google event with exponential backoff retries.

        If Google fails or times out:
        - keeps appointment intact
        - sets calendar_sync_status to pending
        - retries up to max_attempts
        - if still failing, marks failed and alerts tenant admin.
        """
        appointment_id = appointment.get("id")
        delay = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                event_id = await self.create_event(tenant_id, appointment)
                if event_id:
                    return event_id
            except Exception as exc:
                logger.warning(
                    "Attempt %s/%s syncing appointment %s to Google Calendar raised: %s",
                    attempt,
                    max_attempts,
                    appointment_id,
                    exc,
                )

            # Mark as pending after failed attempt
            await store.update_appointment(
                appointment_id, {"calendar_sync_status": "pending"}
            )

            if attempt < max_attempts:
                await asyncio.sleep(delay)
                delay *= 2.0

        # All retries exhausted
        logger.error(
            "Exhausted all %s attempts to sync appointment %s to Google Calendar",
            max_attempts,
            appointment_id,
        )
        await store.update_appointment(
            appointment_id, {"calendar_sync_status": "failed"}
        )
        await self._notify_admin_sync_failed(tenant_id, appointment)
        return None

    async def _mark_appointment_synced(
        self, appointment_id: str, event_id: str, tenant_id: str
    ) -> None:
        """Update appointment record with calendar event ID and synced status."""
        try:
            await store.update_appointment(
                appointment_id,
                {
                    "calendar_event_id": event_id,
                    "calendar_sync_status": "synced",
                },
            )
            await store.update_calendar_connection_status(
                tenant_id,
                status="active",
                last_sync_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            logger.warning("Error marking appointment %s as synced: %s", appointment_id, exc)

    async def _notify_admin_reauth_needed(self, tenant_id: str) -> None:
        """Send notification email to tenant owner about required re-authentication."""
        try:
            tenant = await store.get_tenant(tenant_id)
            owner_email = tenant.get("owner_email") if tenant else None
            if owner_email:
                await self.email_service.send_email(
                    recipient=owner_email,
                    subject="Action Required: Reconnect your Google Calendar - ShipStack Voice",
                    body=(
                        "Hello,\n\n"
                        "Your Google Calendar connection for ShipStack Voice needs to be reconnected.\n"
                        "Please sign in to your dashboard, navigate to Settings > Google Calendar, "
                        "and click Reconnect to continue syncing appointments.\n\n"
                        "- ShipStack Voice Team"
                    ),
                )
        except Exception as exc:
            logger.warning("Failed to send reauth notification email: %s", exc)

    async def _notify_admin_sync_failed(
        self, tenant_id: str, appointment: Dict[str, Any]
    ) -> None:
        """Send notification email to tenant owner when calendar sync fails after retries."""
        try:
            tenant = await store.get_tenant(tenant_id)
            owner_email = tenant.get("owner_email") if tenant else None
            if owner_email:
                customer_name = appointment.get("customer_name", "Customer")
                apt_time = appointment.get("appointment_datetime", "unknown time")
                await self.email_service.send_email(
                    recipient=owner_email,
                    subject="Google Calendar Sync Notice - ShipStack Voice",
                    body=(
                        "Hello,\n\n"
                        f"An appointment for {customer_name} scheduled at {apt_time} was booked successfully "
                        "in ShipStack Voice, but could not be synced to Google Calendar.\n\n"
                        "The booking is safely recorded in your dashboard.\n\n"
                        "- ShipStack Voice Team"
                    ),
                )
        except Exception as exc:
            logger.warning("Failed to send sync failed notification email: %s", exc)

    async def sweep_pending_calendar_syncs(self, limit: int = 50) -> int:
        """Scan for pending calendar sync appointments and retry syncing them.

        Ensures appointments that were pending due to network dropouts or server
        restarts are reliably pushed to Google Calendar.
        """
        try:
            pending_apts = await store.list_pending_calendar_sync_appointments(limit=limit)
        except Exception as exc:
            logger.error("Failed to query pending calendar sync appointments: %s", exc)
            return 0

        processed = 0
        for apt in pending_apts:
            appointment_id = apt.get("id")
            tenant_id = apt.get("tenant_id")
            if not tenant_id or not appointment_id:
                continue

            # Skip if status is cancelled or completed
            if apt.get("status") in ("cancelled", "canceled"):
                await store.update_appointment(
                    appointment_id, {"calendar_sync_status": "skipped"}
                )
                continue

            # Verify active connection exists for the tenant
            connection = await store.get_calendar_connection(tenant_id)
            if not connection:
                logger.info(
                    "No calendar connection for tenant %s; marking appointment %s as not_applicable",
                    tenant_id,
                    appointment_id,
                )
                await store.update_appointment(
                    appointment_id, {"calendar_sync_status": "not_applicable"}
                )
                continue

            if connection.get("status") in ("revoked", "needs_reauth"):
                logger.warning(
                    "Calendar connection for tenant %s is %s; marking appointment %s as failed",
                    tenant_id,
                    connection.get("status"),
                    appointment_id,
                )
                await store.update_appointment(
                    appointment_id, {"calendar_sync_status": "failed"}
                )
                continue

            if connection.get("status") != "active":
                continue

            try:
                logger.info(
                    "Sweeper retrying calendar sync for appointment %s (tenant %s)",
                    appointment_id,
                    tenant_id,
                )
                event_id = await self.sync_appointment_event_with_retry(
                    tenant_id, apt, max_attempts=3
                )
                if event_id:
                    processed += 1
            except Exception as exc:
                logger.warning(
                    "Sweeper failed to sync appointment %s: %s",
                    appointment_id,
                    exc,
                )

        return processed

    async def run_calendar_sync_sweeper(self, interval_seconds: float = 60.0) -> None:
        """Periodic background worker that runs every interval_seconds to retry pending syncs."""
        logger.info("Starting Google Calendar sync sweeper (interval=%.1fs)...", interval_seconds)
        try:
            while True:
                try:
                    processed_count = await self.sweep_pending_calendar_syncs()
                    if processed_count > 0:
                        logger.info("Sweeper successfully synced %d pending appointments", processed_count)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Unexpected error in calendar sync sweeper loop: %s", exc)

                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("Google Calendar sync sweeper background task cancelled.")
            raise


google_calendar_service = GoogleCalendarService()
