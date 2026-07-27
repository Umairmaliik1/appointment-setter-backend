"""SMS App services: leads, campaigns, sending, inbox, and webhook handling.

Reuses the existing per-tenant Twilio integration (encrypted creds) and the
synchronous ``store`` repository layer. The drip engine materializes
``scheduled_sends`` rows that the dedicated SMS worker process claims and
dispatches; this module owns everything except the worker loop itself
(``app/agents/sms_worker.py``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from twilio.base.exceptions import TwilioException
from twilio.rest import Client

from app.api.v1.services.twilio_integration import twilio_integration_service
from app.core import config
from app.core.async_redis import async_redis_client
from app.core.retry import retry_async
from app.services.audit_service import audit_service
from app.services.store import store
from app.utils.phone_number import normalize_phone_number, normalize_phone_number_safe

logger = logging.getLogger(__name__)

SMS_OUTBOUND_ROLE = "sms_outbound"
_MERGE_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def sms_inbox_channel(tenant_id: str) -> str:
    """Redis pub/sub channel the inbox WebSocket subscribes to for a tenant."""
    return f"sms_inbox:tenant:{tenant_id}"


async def _run_twilio_sync(func: Callable) -> Any:
    """Run a synchronous Twilio call off the event loop (mirrors twilio_integration_service)."""
    return await asyncio.to_thread(func)


def render_template(body: str, lead: Dict[str, Any]) -> str:
    """Render ``{{token}}`` placeholders from a lead's fields + merge_fields.

    Unknown tokens render as an empty string so a missing field never leaks raw
    ``{{token}}`` text to a recipient.
    """
    merge_fields = dict(lead.get("merge_fields") or {})
    context: Dict[str, Any] = {
        "name": lead.get("name") or "",
        "phone_number": lead.get("phone_number") or "",
        **merge_fields,
    }

    def _replace(match: "re.Match[str]") -> str:
        key = match.group(1)
        value = context.get(key)
        return "" if value is None else str(value)

    return _MERGE_TOKEN_RE.sub(_replace, body)


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------


class SmsLeadService:
    async def import_lead(self, tenant_id: str, lead: Dict[str, Any]) -> Dict[str, Any]:
        """Create/dedupe a single lead. Idempotent on (tenant_id, phone_number)."""
        normalized = normalize_phone_number(lead["phone_number"])
        payload = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "phone_number": normalized,
            "status": "new",
            "name": lead.get("name"),
            "merge_fields": dict(lead.get("merge_fields") or {}),
            "source": lead.get("source"),
        }
        return await store.upsert_sms_lead(payload)

    async def bulk_import(self, tenant_id: str, leads: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Import many leads, skipping ones with an invalid phone number."""
        created = 0
        skipped: List[Dict[str, Any]] = []
        for lead in leads:
            normalized = normalize_phone_number_safe(lead.get("phone_number"))
            if not normalized:
                skipped.append({"phone_number": lead.get("phone_number"), "reason": "invalid_phone"})
                continue
            await self.import_lead(tenant_id, {**lead, "phone_number": normalized})
            created += 1
        return {"imported": created, "skipped": skipped}

    async def list_leads(self, tenant_id: str, limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
        return await store.list_sms_leads(tenant_id, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Numbers (bind a tenant-owned number for SMS, configure inbound webhook)
# ---------------------------------------------------------------------------


class SmsNumberService:
    async def bind_sms_number(self, tenant_id: str, phone_number: str) -> Dict[str, Any]:
        """Mark a tenant-owned number as SMS-bound and point its sms_url at our inbound webhook."""
        normalized = normalize_phone_number(phone_number)
        phone = await store.get_phone_by_number(normalized)
        if not phone or phone.get("tenant_id") != tenant_id:
            raise ValueError(f"Phone number {normalized} not found for this tenant")

        integration = await twilio_integration_service.get_integration(tenant_id)
        if not integration:
            raise ValueError("Twilio integration not configured for this tenant")

        # Configure the inbound SMS webhook on Twilio (mirrors the voice _configure_webhook pattern).
        if config.SMS_WEBHOOK_BASE_URL:
            inbound_url = f"{config.SMS_WEBHOOK_BASE_URL.rstrip('/')}/api/v1/sms/twilio/inbound"

            def _configure_sms_webhook() -> None:
                client = Client(integration["account_sid"], integration["auth_token"])
                for number in client.incoming_phone_numbers.list():
                    if number.phone_number == normalized:
                        number.update(sms_url=inbound_url, sms_method="POST")
                        break

            try:
                await _run_twilio_sync(_configure_sms_webhook)
            except Exception as exc:  # non-fatal: number still bound in our DB
                logger.warning("Could not configure sms_url for %s: %s", normalized, exc)

        updated = await store.update_phone_number(
            phone["id"],
            {"sms_enabled": True, "sms_usage_role": SMS_OUTBOUND_ROLE},
        )
        return updated or phone


# ---------------------------------------------------------------------------
# Sending (the raw Twilio call + message recording)
# ---------------------------------------------------------------------------


class SmsSendService:
    async def _get_client_creds(self, tenant_id: str) -> Dict[str, str]:
        integration = await twilio_integration_service.get_integration(tenant_id)
        if not integration:
            raise ValueError("Twilio integration not configured for this tenant")
        return {"account_sid": integration["account_sid"], "auth_token": integration["auth_token"]}

    @retry_async(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(TwilioException,))
    async def _create_message(
        self, account_sid: str, auth_token: str, to_number: str, from_number: str, body: str, status_callback: Optional[str]
    ) -> Dict[str, Any]:
        def _send_sync() -> Dict[str, Any]:
            client = Client(account_sid, auth_token)
            kwargs: Dict[str, Any] = {"to": to_number, "from_": from_number, "body": body}
            if status_callback:
                kwargs["status_callback"] = status_callback
            msg = client.messages.create(**kwargs)
            return {"sid": msg.sid, "status": msg.status, "error_code": getattr(msg, "error_code", None)}

        return await _run_twilio_sync(_send_sync)

    async def send(
        self,
        *,
        tenant_id: str,
        to_number: str,
        from_number: str,
        body: str,
        campaign_id: Optional[str] = None,
        lead_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send one SMS via the tenant's Twilio creds and record it. Returns the message dict."""
        creds = await self._get_client_creds(tenant_id)
        status_callback = (
            f"{config.SMS_WEBHOOK_BASE_URL.rstrip('/')}/api/v1/sms/twilio/status"
            if config.SMS_WEBHOOK_BASE_URL
            else None
        )
        result = await self._create_message(
            creds["account_sid"], creds["auth_token"], to_number, from_number, body, status_callback
        )

        message = await store.create_sms_message(
            {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "campaign_id": campaign_id,
                "lead_id": lead_id,
                "direction": "outbound",
                "twilio_sid": result.get("sid"),
                "status": result.get("status"),
                "from_phone_number": from_number,
                "to_phone_number": to_number,
                "body": body,
                "error_code": result.get("error_code"),
            }
        )

        # Record / refresh the conversation thread so the inbox stays current.
        await sms_inbox_service.touch_conversation(
            tenant_id=tenant_id,
            lead_id=lead_id,
            lead_phone_number=to_number,
            tenant_phone_number=from_number,
            inbound=False,
        )
        return message or {"twilio_sid": result.get("sid"), "status": result.get("status")}


# ---------------------------------------------------------------------------
# Campaigns + drip scheduling
# ---------------------------------------------------------------------------


class SmsCampaignService:
    async def create(self, tenant_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        campaign = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "from_phone_number": payload["from_phone_number"],
            "status": "draft",
            "name": payload["name"],
            "steps": payload["steps"],
            "throttle_per_min": payload.get("throttle_per_min") or config.SMS_DEFAULT_THROTTLE_PER_MIN,
            "timezone": payload.get("timezone") or "UTC",
            "respect_quiet_hours": payload.get("respect_quiet_hours", True),
            "lead_ids": payload.get("lead_ids") or [],
        }
        return await store.create_sms_campaign(campaign)

    async def update(self, campaign_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        update = {k: v for k, v in payload.items() if v is not None}
        return await store.update_sms_campaign(campaign_id, update)

    async def pause(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        return await store.update_sms_campaign(campaign_id, {"status": "paused"})

    def _next_send_time(
        self, base: datetime, delay_minutes: int, campaign: Dict[str, Any]
    ) -> datetime:
        """Compute send_after honoring step delay + quiet hours in the campaign timezone."""
        send_at = base + timedelta(minutes=delay_minutes)
        if not campaign.get("respect_quiet_hours", True):
            return send_at
        try:
            tz = ZoneInfo(campaign.get("timezone") or "UTC")
        except Exception:
            tz = ZoneInfo("UTC")
        start = config.SMS_QUIET_HOURS_START
        end = config.SMS_QUIET_HOURS_END
        local = send_at.astimezone(tz)
        hour = local.hour

        def _in_quiet(h: int) -> bool:
            # Window may wrap midnight (e.g. 21 -> 9).
            if start <= end:
                return start <= h < end
            return h >= start or h < end

        if _in_quiet(hour):
            # Push to the next allowed window start (``end`` hour, local).
            next_allowed = local.replace(hour=end, minute=0, second=0, microsecond=0)
            if next_allowed <= local:
                next_allowed = next_allowed + timedelta(days=1)
            return next_allowed.astimezone(timezone.utc)
        return send_at

    async def start(self, tenant_id: str, campaign_id: str) -> Dict[str, Any]:
        """Enroll leads and materialize scheduled_sends for every step. Idempotent."""
        campaign = await store.get_sms_campaign(campaign_id)
        if not campaign or campaign.get("tenant_id") != tenant_id:
            raise ValueError("Campaign not found")

        steps = campaign.get("steps") or []
        if not steps:
            raise ValueError("Campaign has no steps")
        from_number = campaign.get("from_phone_number")
        if not from_number:
            raise ValueError("Campaign has no sending number")

        lead_ids = campaign.get("lead_ids") or []
        enrolled = 0
        scheduled = 0
        suppressed = 0
        now = datetime.now(timezone.utc)

        for lead_id in lead_ids:
            lead = await store.get_sms_lead(lead_id)
            if not lead or lead.get("tenant_id") != tenant_id:
                continue
            to_number = lead["phone_number"]

            # Skip suppressed / opted-out leads entirely.
            if await store.is_sms_suppressed(tenant_id, to_number):
                suppressed += 1
                continue

            enrollment = await store.upsert_sms_enrollment(
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "campaign_id": campaign_id,
                    "lead_id": lead_id,
                    "state": "in_progress",
                    "current_step": 0,
                }
            )
            enrolled += 1

            # Materialize one scheduled_send per step (idempotent via unique constraint).
            base = now
            for step_index, step in enumerate(steps):
                rendered = render_template(step.get("body", ""), lead)
                send_after = self._next_send_time(base, int(step.get("delay_minutes") or 0), campaign)
                base = send_after
                created = await store.create_scheduled_send(
                    {
                        "id": str(uuid.uuid4()),
                        "tenant_id": tenant_id,
                        "campaign_id": campaign_id,
                        "enrollment_id": enrollment["id"],
                        "lead_id": lead_id,
                        "to_phone_number": to_number,
                        "step_index": step_index,
                        "send_after": send_after,
                        "status": "scheduled",
                        "body": rendered,
                        "from_phone_number": from_number,
                    }
                )
                if created:
                    scheduled += 1

        await store.update_sms_campaign(campaign_id, {"status": "running"})
        return {"enrolled": enrolled, "scheduled": scheduled, "suppressed": suppressed}


# ---------------------------------------------------------------------------
# Inbox / conversations
# ---------------------------------------------------------------------------


class SmsInboxService:
    async def touch_conversation(
        self,
        *,
        tenant_id: str,
        lead_id: Optional[str],
        lead_phone_number: str,
        tenant_phone_number: str,
        inbound: bool,
        control_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get-or-create the thread and bump last_message_at / unread_count."""
        convo = await store.upsert_sms_conversation(
            {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "lead_phone_number": lead_phone_number,
                "tenant_phone_number": tenant_phone_number,
            }
        )
        update: Dict[str, Any] = {"last_message_at": datetime.now(timezone.utc)}
        if inbound:
            update["unread_count"] = int(convo.get("unread_count") or 0) + 1
        if control_mode:
            update["control_mode"] = control_mode
        updated = await store.update_sms_conversation(convo["id"], update)
        return updated or convo

    async def list_conversations(self, tenant_id: str, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        return await store.list_sms_conversations(tenant_id, limit=limit, offset=offset)

    async def get_conversation_detail(self, tenant_id: str, conversation_id: str) -> Optional[Dict[str, Any]]:
        convo = await store.get_sms_conversation(conversation_id)
        if not convo or convo.get("tenant_id") != tenant_id:
            return None
        messages: List[Dict[str, Any]] = []
        if convo.get("lead_id"):
            messages = await store.list_sms_messages_for_lead(tenant_id, convo["lead_id"])
        # Mark read on open.
        await store.update_sms_conversation(conversation_id, {"unread_count": 0})
        return {**convo, "unread_count": 0, "messages": messages}

    async def manual_reply(self, tenant_id: str, conversation_id: str, body: str) -> Dict[str, Any]:
        """Human take-over reply. Sets control_mode=human and sends from the thread's number."""
        convo = await store.get_sms_conversation(conversation_id)
        if not convo or convo.get("tenant_id") != tenant_id:
            raise ValueError("Conversation not found")
        await store.update_sms_conversation(conversation_id, {"control_mode": "human", "unread_count": 0})
        return await sms_send_service.send(
            tenant_id=tenant_id,
            to_number=convo["lead_phone_number"],
            from_number=convo["tenant_phone_number"],
            body=body,
            lead_id=convo.get("lead_id"),
        )


# ---------------------------------------------------------------------------
# Suppressions
# ---------------------------------------------------------------------------


class SmsSuppressionService:
    async def add(self, tenant_id: str, phone_number: str, reason: str = "manual") -> Dict[str, Any]:
        normalized = normalize_phone_number(phone_number)
        suppression = await store.add_sms_suppression(
            {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "phone_number": normalized,
                "reason": reason,
            }
        )
        # Cancel any in-flight sends and mark the matching lead opted out.
        lead = await store.get_sms_lead_by_phone(tenant_id, normalized)
        if lead:
            await store.cancel_pending_sends_for_lead(tenant_id, lead["id"])
            await store.update_sms_lead(lead["id"], {"status": "opted_out"})
        return suppression

    async def list(self, tenant_id: str, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        return await store.list_sms_suppressions(tenant_id, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Webhook handling (inbound + status), shared by the router endpoints
# ---------------------------------------------------------------------------


class SmsWebhookService:
    def _is_stop(self, body: str) -> bool:
        word = (body or "").strip().upper()
        return word in config.SMS_STOP_KEYWORDS

    def _is_start(self, body: str) -> bool:
        word = (body or "").strip().upper()
        return word in config.SMS_START_KEYWORDS

    async def handle_inbound(self, form: Dict[str, Any]) -> None:
        """Process an inbound SMS: record, opt-out on STOP, else pause + flag human + notify."""
        message_sid = form.get("MessageSid") or form.get("SmsSid")
        from_number = normalize_phone_number_safe(form.get("From")) or form.get("From")
        to_number = normalize_phone_number_safe(form.get("To")) or form.get("To")
        body = form.get("Body") or ""

        # Resolve tenant via the receiving (To) number's phone record.
        phone_record = await store.get_phone_by_number(to_number) if to_number else None
        tenant_id = phone_record.get("tenant_id") if phone_record else None
        if not tenant_id:
            logger.warning("Inbound SMS to unknown number %s — ignoring", to_number)
            return

        lead = await store.get_sms_lead_by_phone(tenant_id, from_number) if from_number else None
        lead_id = lead["id"] if lead else None

        # Record inbound message (idempotent on MessageSid).
        await store.create_sms_message(
            {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "direction": "inbound",
                "twilio_sid": message_sid,
                "status": "received",
                "from_phone_number": from_number,
                "to_phone_number": to_number,
                "body": body,
            }
        )

        # Always reflect the inbound message in the inbox.
        convo = await sms_inbox_service.touch_conversation(
            tenant_id=tenant_id,
            lead_id=lead_id,
            lead_phone_number=from_number,
            tenant_phone_number=to_number,
            inbound=True,
        )

        if self._is_start(body):
            # Opt back in is left to manual review; just log for now.
            logger.info("Inbound START from %s (tenant %s)", from_number, tenant_id)
            return

        if self._is_stop(body):
            await store.add_sms_suppression(
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "phone_number": from_number,
                    "reason": "opted_out",
                }
            )
            if lead_id:
                await store.cancel_pending_sends_for_lead(tenant_id, lead_id)
                await store.update_sms_lead(lead_id, {"status": "opted_out"})
                for enrollment in await store.list_sms_enrollments_for_lead(tenant_id, lead_id):
                    await store.update_sms_enrollment(enrollment["id"], {"state": "opted_out"})
            await audit_service.log_event(
                actor={"id": "system", "email": "", "role": "system"},
                action="sms.lead.opted_out",
                resource_type="sms_lead",
                resource_id=lead_id or from_number,
                metadata={"tenant_id": tenant_id, "phone_number": from_number},
            )
            return

        # Regular reply → take-over: pause sequence, flag conversation human, notify.
        if lead_id:
            await store.cancel_pending_sends_for_lead(tenant_id, lead_id)
            await store.update_sms_lead(lead_id, {"status": "replied"})
            for enrollment in await store.list_sms_enrollments_for_lead(tenant_id, lead_id):
                if enrollment.get("state") in {"pending", "in_progress"}:
                    await store.update_sms_enrollment(enrollment["id"], {"state": "paused_on_reply"})
        await store.update_sms_conversation(convo["id"], {"control_mode": "human"})

        # Best-effort notification (never blocks webhook).
        try:
            await sms_notification_service.notify_reply(tenant_id, convo, body)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to dispatch SMS reply notification: %s", exc)

    async def handle_status(self, form: Dict[str, Any]) -> None:
        """Update an outbound message's delivery status (idempotent on MessageSid)."""
        message_sid = form.get("MessageSid") or form.get("SmsSid")
        status = form.get("MessageStatus") or form.get("SmsStatus")
        error_code = form.get("ErrorCode")
        if not message_sid or not status:
            return
        await store.update_sms_message_status_by_sid(
            message_sid, status, extra={"error_code": error_code} if error_code else None
        )


class SmsNotificationService:
    """Best-effort reply notification: real-time Redis pub/sub for the inbox UI,
    plus a fallback email to the tenant owner. Never blocks the webhook path."""

    async def _publish_inbox_event(self, tenant_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        """Publish to the tenant inbox channel (mirrors live_chat_service._publish_event)."""
        try:
            client = await async_redis_client.get_client()
            await client.publish(sms_inbox_channel(tenant_id), json.dumps({"type": event_type, **payload}))
        except Exception as exc:  # pragma: no cover - pub/sub is best-effort
            logger.warning("Failed to publish SMS inbox event: %s", exc)

    async def _email_tenant_owner(self, tenant_id: str, conversation: Dict[str, Any], body: str) -> None:
        """Email the tenant owner that a lead replied (fallback when no one is watching)."""
        try:
            tenant = await store.get_tenant(tenant_id)
            owner_email = None
            if tenant:
                owner_email = (tenant.get("data") or {}).get("owner_email") or tenant.get("owner_email")
            if not owner_email:
                return
            from app.services.email.service import EmailService

            preview = (body or "").strip()[:200]
            await EmailService().send_raw_email(
                to_email=owner_email,
                subject="New SMS reply — a lead is waiting",
                body=(
                    f"A lead ({conversation.get('lead_phone_number')}) replied to your SMS outreach.\n\n"
                    f"\"{preview}\"\n\n"
                    "Open the SMS inbox to take over the conversation."
                ),
            )
        except Exception as exc:  # pragma: no cover - email is best-effort
            logger.warning("Failed to email SMS reply notification: %s", exc)

    async def notify_reply(self, tenant_id: str, conversation: Dict[str, Any], body: str) -> None:
        logger.info(
            "SMS reply take-over: tenant=%s conversation=%s from=%s",
            tenant_id,
            conversation.get("id"),
            conversation.get("lead_phone_number"),
        )
        # Real-time push to any connected inbox; email as the durable fallback.
        await self._publish_inbox_event(
            tenant_id,
            "sms.reply",
            {"conversation": conversation, "preview": (body or "").strip()[:200]},
        )
        await self._email_tenant_owner(tenant_id, conversation, body)


# Global singletons (match existing service style).
sms_number_service = SmsNumberService()
sms_lead_service = SmsLeadService()
sms_send_service = SmsSendService()
sms_campaign_service = SmsCampaignService()
sms_inbox_service = SmsInboxService()
sms_suppression_service = SmsSuppressionService()
sms_notification_service = SmsNotificationService()
sms_webhook_service = SmsWebhookService()
