"""Email service — SMTP transport via fastapi_mail.

Required environment (read via `EmailSettings`):
  * MAIL_USERNAME, MAIL_PASSWORD  — SMTP credentials.
  * MAIL_SERVER, MAIL_PORT        — host / port (e.g. smtp.gmail.com / 587).
  * MAIL_FROM                     — From header.
  * MAIL_STARTTLS, MAIL_SSL_TLS   — transport security flags.
  * USE_CREDENTIALS, VALIDATE_CERTS

Gmail-From defense:
  Gmail's SMTP relay rejects sends where the `From` header doesn't match
  the authenticated MAIL_USERNAME unless that address is registered as a
  verified "Send mail as" alias on the Gmail account. To prevent the
  silent failure mode (booking succeeds but the email is rejected at the
  SMTP layer), this service detects MAIL_SERVER == smtp.gmail.com /
  smtp.googlemail.com and, when MAIL_FROM doesn't match MAIL_USERNAME,
  silently overrides the From to MAIL_USERNAME and logs ONE warning at
  startup so the operator can fix the .env or switch to a real
  transactional provider.
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType

from app.core.config import email_settings
from app.services.email.templates import EmailTemplates

logger = logging.getLogger(__name__)


class EmailService:
    """SMTP-backed email service.

    Public method signatures are stable across the codebase's transport
    history (this is the path of record again now); callers — the voice
    worker, appointment service, auth flow, and the existing test suite —
    need no edits when this module is swapped.
    """

    def __init__(self) -> None:
        # Gmail-From defense: override From -> MAIL_USERNAME if the SMTP
        # server is Gmail and the two don't match.
        effective_from = email_settings.MAIL_FROM
        server = (email_settings.MAIL_SERVER or "").strip().lower()
        is_gmail = server in ("smtp.gmail.com", "smtp.googlemail.com")
        if (
            is_gmail
            and email_settings.MAIL_USERNAME
            and effective_from
            and effective_from.strip().lower() != email_settings.MAIL_USERNAME.strip().lower()
        ):
            logger.warning(
                "[EMAIL] Gmail SMTP requires MAIL_FROM to match the "
                "authenticated MAIL_USERNAME; overriding From '%s' -> '%s' "
                "so mail still sends. Update .env (or set up 'Send mail as' "
                "in Gmail) to silence this.",
                effective_from, email_settings.MAIL_USERNAME,
            )
            effective_from = email_settings.MAIL_USERNAME

        self._from_email = effective_from

        self.conf = ConnectionConfig(
            MAIL_USERNAME=email_settings.MAIL_USERNAME,
            MAIL_PASSWORD=email_settings.MAIL_PASSWORD,
            MAIL_FROM=effective_from,
            MAIL_PORT=email_settings.MAIL_PORT,
            MAIL_SERVER=email_settings.MAIL_SERVER,
            MAIL_STARTTLS=email_settings.MAIL_STARTTLS,
            MAIL_SSL_TLS=email_settings.MAIL_SSL_TLS,
            USE_CREDENTIALS=email_settings.USE_CREDENTIALS,
            VALIDATE_CERTS=email_settings.VALIDATE_CERTS,
        )
        self.fm = FastMail(self.conf)

        logger.info(
            "[EMAIL] transport=smtp server=%s from=%s",
            email_settings.MAIL_SERVER or "<unset>",
            self._from_email or "<unset>",
        )

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    async def _send(self, subject: str, recipients: List[str], html_body: str) -> None:
        """Send via SMTP; raises on hard failure.

        Callers (the public send_* methods below) catch and return False so
        a single email hiccup never sinks a booking.
        """
        if not recipients:
            raise ValueError("send() called with no recipients")

        message = MessageSchema(
            subject=subject,
            recipients=recipients,
            body=html_body,
            subtype=MessageType.html,
        )
        try:
            await self.fm.send_message(message)
        except Exception as exc:
            logger.error(
                "[EMAIL] smtp send failed for to=%s subject=%r: %s",
                recipients, subject, exc, exc_info=True,
            )
            raise
        logger.info("[EMAIL] sent (smtp) to=%s subject=%r", recipients, subject)

    # ------------------------------------------------------------------
    # Public send_* methods (signatures stable across transport swaps)
    # ------------------------------------------------------------------
    async def send_raw_email(self, to_email: str, subject: str, body: str) -> str:
        """Generic raw send used by the AI agent tool."""
        try:
            html = EmailTemplates.generic_submission(to_email or "AI Agent User", body, "N/A")
            await self._send(subject, [to_email], html)
            return f"Email successfully sent to {to_email}"
        except Exception as e:
            return f"Failed to send email: {str(e)}"

    async def send_appointment_confirmation(
        self,
        customer_email: str,
        customer_name: str,
        appointment_datetime: datetime,
        service_type: str,
        service_address: str,
        appointment_id: Optional[str] = None,
        service_details: Optional[str] = None,
        business_name: Optional[str] = None,
    ) -> bool:
        try:
            data = {
                "customer_name": customer_name,
                "service_type": service_type,
                "appointment_datetime": appointment_datetime.strftime("%B %d, %Y at %I:%M %p"),
                "service_address": service_address,
                "appointment_id": appointment_id or "N/A",
                "service_details": service_details,
                "business_name": business_name or "AI Phone Scheduler",
            }
            subject, html = EmailTemplates.appointment_confirmation(data)
            await self._send(subject, [customer_email], html)
            return True
        except Exception as e:
            logger.error(f"Error sending appointment confirmation email: {e}", exc_info=True)
            return False

    async def send_appointment_owner_notification(
        self,
        owner_email: str,
        customer_name: str,
        customer_email: str,
        appointment_datetime: datetime,
        service_type: str,
        service_address: str,
        appointment_id: Optional[str] = None,
        service_details: Optional[str] = None,
        business_name: Optional[str] = None,
    ) -> bool:
        try:
            data = {
                "customer_name": customer_name,
                "customer_email": customer_email,
                "service_type": service_type,
                "appointment_datetime": appointment_datetime.strftime("%B %d, %Y at %I:%M %p"),
                "service_address": service_address,
                "appointment_id": appointment_id or "N/A",
                "service_details": service_details,
                "business_name": business_name or "AI Phone Scheduler",
            }
            subject, html = EmailTemplates.appointment_owner_notification(data)
            await self._send(subject, [owner_email], html)
            return True
        except Exception as e:
            logger.error(f"Error sending appointment owner email: {e}", exc_info=True)
            return False

    async def send_appointment_cancellation(
        self,
        customer_email: str,
        customer_name: str,
        appointment_datetime: datetime,
        reason: Optional[str] = None,
        service_type: Optional[str] = None,
        business_name: Optional[str] = None,
    ) -> bool:
        try:
            data = {
                "customer_name": customer_name,
                "service_type": service_type or "Service",
                "appointment_datetime": appointment_datetime.strftime("%B %d, %Y at %I:%M %p"),
                "old_status": "scheduled",
                "new_status": "cancelled",
                "appointment_id": "N/A",
                "business_name": business_name or "AI Phone Scheduler",
                "cancellation_reason": reason,
            }
            subject, html = EmailTemplates.appointment_status_update(data)
            await self._send(subject, [customer_email], html)
            return True
        except Exception as e:
            logger.error(f"Error sending appointment cancellation email: {e}", exc_info=True)
            return False

    async def send_appointment_reschedule(
        self,
        customer_email: str,
        customer_name: str,
        new_datetime: datetime,
        reason: Optional[str] = None,
        old_datetime: Optional[datetime] = None,
        service_type: Optional[str] = None,
        service_address: Optional[str] = None,
        appointment_id: Optional[str] = None,
        business_name: Optional[str] = None,
    ) -> bool:
        try:
            data = {
                "customer_name": customer_name,
                "service_type": service_type or "Service",
                "old_datetime": old_datetime.strftime("%B %d, %Y at %I:%M %p") if old_datetime else "N/A",
                "new_datetime": new_datetime.strftime("%B %d, %Y at %I:%M %p"),
                "service_address": service_address or "N/A",
                "appointment_id": appointment_id or "N/A",
                "business_name": business_name or "AI Phone Scheduler",
                "reschedule_reason": reason,
            }
            subject, html = EmailTemplates.appointment_reschedule(data)
            await self._send(subject, [customer_email], html)
            return True
        except Exception as e:
            logger.error(f"Error sending appointment reschedule email: {e}", exc_info=True)
            return False

    async def send_partner_owner_invite(
        self,
        *,
        owner_email: str,
        owner_name: str,
        partner_name: str,
        setup_password_url: str,
        login_url: str,
        expires_in_hours: int = 48,
        platform_name: str = "MindRind",
    ) -> bool:
        """Onboarding invite for a new partner owner."""
        try:
            subject, html = EmailTemplates.partner_owner_invite(
                {
                    "owner_name": owner_name,
                    "owner_email": owner_email,
                    "partner_name": partner_name,
                    "setup_password_url": setup_password_url,
                    "login_url": login_url,
                    "expires_in_hours": expires_in_hours,
                    "platform_name": platform_name,
                }
            )
            await self._send(subject, [owner_email], html)
            return True
        except Exception as e:
            logger.error(f"Error sending partner owner invite email: {e}", exc_info=True)
            return False

    async def send_user_setup_invite(
        self,
        *,
        recipient_email: str,
        recipient_name: str,
        workspace_name: str,
        setup_password_url: str,
        login_url: str,
        expires_in_hours: int = 48,
        platform_name: str = "MindRind",
    ) -> bool:
        """Setup invite for a newly onboarded workspace user."""
        try:
            subject, html = EmailTemplates.user_setup_invite(
                {
                    "recipient_email": recipient_email,
                    "recipient_name": recipient_name,
                    "workspace_name": workspace_name,
                    "setup_password_url": setup_password_url,
                    "login_url": login_url,
                    "expires_in_hours": expires_in_hours,
                    "platform_name": platform_name,
                }
            )
            await self._send(subject, [recipient_email], html)
            return True
        except Exception as e:
            logger.error(f"Error sending user setup invite email: {e}", exc_info=True)
            return False

    async def send_lead_notification(
        self,
        team_email: str,
        lead_data: dict,
    ) -> bool:
        """Generic new-lead team notification (vertical-agnostic).

        `lead_data` shape — see EmailTemplates.lead_notification. Returns
        True on a successful SMTP delivery, False otherwise; the caller
        uses the bool to choose the LLM's closing line.
        """
        try:
            subject, html = EmailTemplates.lead_notification(lead_data)
            await self._send(subject, [team_email], html)
            return True
        except Exception as e:
            logger.error("Error sending lead notification email: %s", e, exc_info=True)
            return False

    async def send_password_reset_email(
        self,
        *,
        recipient_email: str,
        recipient_name: str,
        reset_password_url: str,
        expires_in_minutes: int = 60,
        platform_name: str = "MindRind",
    ) -> bool:
        """Password reset email."""
        try:
            subject, html = EmailTemplates.password_reset(
                {
                    "recipient_email": recipient_email,
                    "recipient_name": recipient_name,
                    "reset_password_url": reset_password_url,
                    "expires_in_minutes": expires_in_minutes,
                    "platform_name": platform_name,
                }
            )
            await self._send(subject, [recipient_email], html)
            return True
        except Exception as e:
            logger.error(f"Error sending password reset email: {e}", exc_info=True)
            return False


email_service = EmailService()
