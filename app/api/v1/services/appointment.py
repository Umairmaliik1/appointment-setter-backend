"""
Appointment service for managing appointments, Google Calendar sync, and notifications.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.api.v1.services.scheduling import SchedulingService
from app.core.datetime_utils import parse_to_utc_datetime, to_utc_iso_z
from app.services.email.service import EmailService
from app.services.google_calendar import google_calendar_service
from app.services.store import store

logger = logging.getLogger(__name__)


class AppointmentService:
    """Service class for appointment operations using PostgreSQL and Google Calendar."""

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
        sync_calendar: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Create a new appointment and queue Google Calendar sync in background."""
        try:
            # Validate appointment time
            is_valid, error_message = await self.scheduling_service.validate_appointment_time(
                tenant_id, appointment_datetime, duration_minutes
            )

            if not is_valid:
                raise ValueError(error_message)

            utc_dt_str = to_utc_iso_z(appointment_datetime)
            now_utc_str = to_utc_iso_z(datetime.now(timezone.utc))

            customer_org = await store.get_org_by_legacy_tenant_id(tenant_id, prefer_customer=True)

            # Check if tenant has an active Google Calendar connection
            cal_conn = None
            if sync_calendar:
                try:
                    cal_conn = await store.get_calendar_connection(tenant_id)
                except Exception as c_err:
                    logger.warning("Error checking calendar connection for tenant %s: %s", tenant_id, c_err)

            is_cal_active = bool(cal_conn and cal_conn.get("status") == "active")
            initial_sync_status = "pending" if is_cal_active else "not_applicable"

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
                "appointment_datetime": utc_dt_str,
                "duration_minutes": duration_minutes,
                "service_details": service_details,
                "status": "scheduled",
                "calendar_event_id": None,
                "calendar_sync_status": initial_sync_status,
                "appointment_data": appointment_data or {},
                "created_at": now_utc_str,
                "updated_at": now_utc_str,
            }

            appointment = await store.create_appointment(appointment_dict)
            if not appointment:
                return None

            # Confirmation email
            if send_email and customer_email:
                await self.email_service.send_appointment_confirmation(
                    customer_email, customer_name, appointment_datetime, service_type, service_address
                )

            # Start background Google Calendar event creation if sync is enabled and calendar is active
            if sync_calendar and is_cal_active:
                try:
                    asyncio.create_task(
                        asyncio.shield(
                            google_calendar_service.sync_appointment_event_with_retry(
                                tenant_id, appointment
                            )
                        )
                    )
                except Exception as sync_err:
                    logger.warning("Failed to start calendar sync task for %s: %s", appointment["id"], sync_err)

            return appointment

        except Exception as e:
            logger.error("Error creating appointment: %s", e, exc_info=True)
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
        appointments = await store.list_appointments(tenant_id, limit, offset)

        filtered_appointments = []
        for appointment in appointments:
            if status and appointment.get("status") != status:
                continue

            apt_dt = parse_to_utc_datetime(appointment.get("appointment_datetime"))
            if not apt_dt:
                continue

            if start_date:
                start_utc = start_date if start_date.tzinfo else start_date.replace(tzinfo=timezone.utc)
                if apt_dt < start_utc:
                    continue

            if end_date:
                end_utc = end_date if end_date.tzinfo else end_date.replace(tzinfo=timezone.utc)
                if apt_dt > end_utc:
                    continue

            filtered_appointments.append(appointment)

        return filtered_appointments

    async def update_appointment_status(
        self, appointment_id: str, status: str, notes: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Update appointment status."""
        update_data = {
            "status": status,
            "updated_at": to_utc_iso_z(datetime.now(timezone.utc)),
        }

        if notes:
            update_data["notes"] = notes

        return await store.update_appointment(appointment_id, update_data)

    async def cancel_appointment(self, appointment_id: str, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Cancel an appointment and delete its Google Calendar event."""
        appointment = await self.get_appointment(appointment_id)
        if not appointment:
            return None

        tenant_id = appointment.get("tenant_id")
        event_id = appointment.get("calendar_event_id")

        update_data = {
            "status": "cancelled",
            "cancelled_at": to_utc_iso_z(datetime.now(timezone.utc)),
            "updated_at": to_utc_iso_z(datetime.now(timezone.utc)),
        }

        if reason:
            update_data["cancellation_reason"] = reason

        updated = await store.update_appointment(appointment_id, update_data)

        # Delete from Google Calendar in background (shielded)
        if tenant_id and event_id:
            try:
                asyncio.create_task(
                    asyncio.shield(google_calendar_service.delete_event(tenant_id, event_id))
                )
            except Exception as del_err:
                logger.warning("Failed to start Google Calendar delete event task: %s", del_err)

        # Send cancellation email if customer email exists
        if updated and updated.get("customer_email"):
            apt_dt = parse_to_utc_datetime(updated.get("appointment_datetime")) or datetime.now(timezone.utc)
            await self.email_service.send_appointment_cancellation(
                updated["customer_email"],
                updated["customer_name"],
                apt_dt,
                reason,
            )

        return updated

    async def reschedule_appointment(
        self, appointment_id: str, new_datetime: datetime, reason: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Reschedule an appointment and update its Google Calendar event."""
        appointment = await self.get_appointment(appointment_id)
        if not appointment:
            return None

        tenant_id = appointment["tenant_id"]
        duration = appointment.get("duration_minutes", 60)

        is_valid, error_message = await self.scheduling_service.validate_appointment_time(
            tenant_id, new_datetime, duration
        )

        if not is_valid:
            raise ValueError(error_message)

        utc_dt_str = to_utc_iso_z(new_datetime)
        update_data = {
            "appointment_datetime": utc_dt_str,
            "status": "rescheduled",
            "updated_at": to_utc_iso_z(datetime.now(timezone.utc)),
        }

        if reason:
            update_data["reschedule_reason"] = reason

        updated_appointment = await store.update_appointment(appointment_id, update_data)

        # Update Google Calendar event in background (shielded)
        event_id = updated_appointment.get("calendar_event_id")
        if event_id:
            try:
                asyncio.create_task(
                    asyncio.shield(
                        google_calendar_service.update_event(
                            tenant_id, event_id, updated_appointment
                        )
                    )
                )
            except Exception as patch_err:
                logger.warning("Failed to start Google Calendar update event task: %s", patch_err)
        else:
            try:
                asyncio.create_task(
                    asyncio.shield(
                        google_calendar_service.sync_appointment_event_with_retry(
                            tenant_id, updated_appointment
                        )
                    )
                )
            except Exception as sync_err:
                logger.warning("Failed to start calendar sync task on reschedule: %s", sync_err)

        # Send reschedule email if customer email exists
        if updated_appointment and updated_appointment.get("customer_email"):
            apt_dt = parse_to_utc_datetime(updated_appointment.get("appointment_datetime")) or datetime.now(timezone.utc)
            await self.email_service.send_appointment_reschedule(
                updated_appointment["customer_email"],
                updated_appointment["customer_name"],
                apt_dt,
                reason,
            )

        return updated_appointment

    async def complete_appointment(
        self, appointment_id: str, completion_notes: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Mark appointment as completed."""
        update_data = {
            "status": "completed",
            "completed_at": to_utc_iso_z(datetime.now(timezone.utc)),
            "updated_at": to_utc_iso_z(datetime.now(timezone.utc)),
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


appointment_service = AppointmentService()
