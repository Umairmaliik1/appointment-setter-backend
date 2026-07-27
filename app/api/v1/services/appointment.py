"""
Appointment service for managing appointments and email notifications using PostgreSQL.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.api.v1.services.scheduling import SchedulingService
from app.core.response_mappers import parse_iso_timestamp
from app.services.email.service import EmailService
from app.services.store import store

# Configure logging
logger = logging.getLogger(__name__)


class AppointmentService:
    """Service class for appointment operations using PostgreSQL."""

    def __init__(self):
        """Initialize appointment service."""
        self.scheduling_service = SchedulingService()
        self.email_service = EmailService()

    async def create_appointment(
        self,
        tenant_id: str,
        customer_name: str,
        customer_phone: str,
        customer_email: Optional[str],
        service_type: str,
        service_address: str,
        appointment_datetime: datetime,
        service_details: Optional[str] = None,
        appointment_data: Optional[Dict[str, Any]] = None,
        call_id: Optional[str] = None,
        duration_minutes: int = 60,
        send_email: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Create a new appointment.

        When `send_email` is True (default) the customer confirmation email
        is sent as part of the call. Callers that need visibility into
        email success/failure (e.g. the voice worker, which has to tell
        the caller whether to expect an email) should pass `send_email=False`
        and call `email_service.send_appointment_confirmation` themselves.
        """
        try:
            # Validate appointment time
            is_valid, error_message = await self.scheduling_service.validate_appointment_time(
                tenant_id, appointment_datetime, duration_minutes
            )

            if not is_valid:
                raise ValueError(error_message)

            # Create appointment
            customer_org = await store.get_org_by_legacy_tenant_id(tenant_id, prefer_customer=True)
            appointment_dict = {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "legacy_tenant_id": tenant_id,
                "customer_org_id": customer_org.get("id") if customer_org else None,
                "call_id": call_id,
                "customer_name": customer_name,
                "customer_phone": customer_phone,
                "customer_email": customer_email,
                "service_type": service_type,
                "service_address": service_address,
                "appointment_datetime": appointment_datetime.isoformat(),
                "duration_minutes": duration_minutes,
                "service_details": service_details,
                "status": "scheduled",
                "appointment_data": appointment_data or {},
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            appointment = await store.create_appointment(appointment_dict)

            # Send confirmation email — opt-out for callers that own the
            # email path themselves (the voice worker does, so it can report
            # the actual delivery status back to the LLM).
            if send_email and customer_email:
                await self.email_service.send_appointment_confirmation(
                    customer_email, customer_name, appointment_datetime, service_type, service_address
                )

            return appointment

        except Exception as e:
            logger.error(f"Error creating appointment: {e}", exc_info=True)
            return None

    async def get_appointment(self, appointment_id: str) -> Optional[Dict[str, Any]]:
        """Get appointment by ID."""
        return await store.get_appointment(appointment_id)

    async def list_appointments(
        self,
        tenant_id: str,
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List appointments for a tenant with filters."""
        # Get base appointments
        appointments = await store.list_appointments(tenant_id, limit, offset)

        # Apply filters
        filtered_appointments = []
        for appointment in appointments:
            # Status filter
            if status and appointment.get("status") != status:
                continue

            # Date filters
            appointment_datetime = parse_iso_timestamp(appointment["appointment_datetime"])
            if not appointment_datetime:
                continue

            if start_date and appointment_datetime < start_date:
                continue

            if end_date and appointment_datetime > end_date:
                continue

            filtered_appointments.append(appointment)

        return filtered_appointments

    async def update_appointment_status(
        self, appointment_id: str, status: str, notes: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Update appointment status."""
        update_data = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}

        if notes:
            update_data["notes"] = notes

        return await store.update_appointment(appointment_id, update_data)

    async def cancel_appointment(self, appointment_id: str, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Cancel an appointment."""
        update_data = {
            "status": "cancelled",
            "cancelled_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if reason:
            update_data["cancellation_reason"] = reason

        appointment = await store.update_appointment(appointment_id, update_data)

        # Send cancellation email if customer email exists
        if appointment and appointment.get("customer_email"):
            await self.email_service.send_appointment_cancellation(
                appointment["customer_email"],
                appointment["customer_name"],
                parse_iso_timestamp(appointment["appointment_datetime"]) or datetime.now(timezone.utc),
                reason,
            )

        return appointment

    async def reschedule_appointment(
        self, appointment_id: str, new_datetime: datetime, reason: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Reschedule an appointment."""
        # Validate new time
        appointment = await self.get_appointment(appointment_id)
        if not appointment:
            return None

        is_valid, error_message = await self.scheduling_service.validate_appointment_time(
            appointment["tenant_id"], new_datetime, appointment.get("duration_minutes", 60)
        )

        if not is_valid:
            raise ValueError(error_message)

        update_data = {
            "appointment_datetime": new_datetime.isoformat(),
            "status": "rescheduled",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if reason:
            update_data["reschedule_reason"] = reason

        updated_appointment = await store.update_appointment(appointment_id, update_data)

        # Send reschedule email if customer email exists
        if updated_appointment and updated_appointment.get("customer_email"):
            await self.email_service.send_appointment_reschedule(
                updated_appointment["customer_email"],
                updated_appointment["customer_name"],
                parse_iso_timestamp(updated_appointment["appointment_datetime"]) or datetime.now(timezone.utc),
                reason,
            )

        return updated_appointment

    async def complete_appointment(
        self, appointment_id: str, completion_notes: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Mark appointment as completed."""
        update_data = {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        if completion_notes:
            update_data["completion_notes"] = completion_notes

        return await store.update_appointment(appointment_id, update_data)

    async def get_appointments_by_date_range(
        self, tenant_id: str, start_date: datetime, end_date: datetime
    ) -> List[Dict[str, Any]]:
        """Get appointments within a date range."""
        return await self.list_appointments(tenant_id=tenant_id, start_date=start_date, end_date=end_date)

    async def get_upcoming_appointments(self, tenant_id: str, days_ahead: int = 7) -> List[Dict[str, Any]]:
        """Get upcoming appointments for the next N days."""
        start_date = datetime.now(timezone.utc)
        end_date = start_date + timedelta(days=days_ahead)

        return await self.list_appointments(tenant_id=tenant_id, start_date=start_date, end_date=end_date, status="scheduled")


# Global appointment service instance
appointment_service = AppointmentService()
