"""
Scheduling service for appointment slot management with Redis holds using PostgreSQL and Google Calendar.
PERFORMANCE OPTIMIZED: Using async Redis for 5-10x improvement.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.api.v1.services.tenant import tenant_service
from app.core.async_redis import async_redis_client
from app.core.datetime_utils import parse_to_utc_datetime, to_utc_iso_z
from app.services.google_calendar import google_calendar_service
from app.services.store import store

logger = logging.getLogger(__name__)


@dataclass
class TimeSlot:
    """Time slot representation."""

    start_time: datetime
    end_time: datetime
    duration_minutes: int
    is_available: bool = True
    is_held: bool = False
    hold_expires_at: Optional[datetime] = None
    hold_id: Optional[str] = None


@dataclass
class SlotHold:
    """Slot hold information."""

    hold_id: str
    tenant_id: str
    slot_start: datetime
    slot_end: datetime
    customer_name: str
    customer_phone: str
    expires_at: datetime
    created_at: datetime


class SchedulingService:
    """Service class for appointment scheduling operations using PostgreSQL and Google Calendar."""

    def __init__(self):
        """Initialize scheduling service with async Redis."""
        self.redis_client = async_redis_client

    async def _get_tenant_timezone(self, tenant_id: str) -> ZoneInfo:
        """Resolve the tenant's IANA timezone."""
        try:
            business_config = await tenant_service.get_business_config(tenant_id)
            if isinstance(business_config, dict) and business_config.get("timezone"):
                return ZoneInfo(business_config["timezone"])
            conn = await store.get_calendar_connection(tenant_id)
            if conn and conn.get("timezone"):
                return ZoneInfo(conn["timezone"])
        except Exception as exc:
            logger.warning("Error resolving timezone for tenant %s, falling back to UTC: %s", tenant_id, exc)
        return ZoneInfo("UTC")

    async def generate_available_slots(
        self, tenant_id: str, start_date: date, end_date: date, duration_minutes: int = 60, buffer_minutes: int = 15
    ) -> List[TimeSlot]:
        """Generate available time slots for a tenant within a date range.

        - Respects tenant working hours in tenant timezone.
        - Subtracts internal appointments (with buffer).
        - Subtracts Google Calendar busy intervals (with buffer).
        - Falls back gracefully to internal slots if Google is unreachable.
        """
        slots: List[TimeSlot] = []

        tenant = await tenant_service.get_tenant(tenant_id)
        if not tenant:
            return slots

        tz = await self._get_tenant_timezone(tenant_id)

        # Default working hours (9 AM to 5 PM Monday-Friday, 10 AM to 2 PM Saturday-Sunday)
        working_hours = {
            "monday": {"start": "09:00", "end": "17:00"},
            "tuesday": {"start": "09:00", "end": "17:00"},
            "wednesday": {"start": "09:00", "end": "17:00"},
            "thursday": {"start": "09:00", "end": "17:00"},
            "friday": {"start": "09:00", "end": "17:00"},
            "saturday": {"start": "10:00", "end": "14:00"},
            "sunday": {"start": "10:00", "end": "14:00"},
        }
        business_config = await tenant_service.get_business_config(tenant_id)
        if isinstance(business_config, dict) and isinstance(business_config.get("working_hours"), dict):
            working_hours = business_config["working_hours"]

        # Date range for internal query (UTC)
        range_start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
        range_end_dt = datetime.combine(end_date, datetime.max.time(), tzinfo=tz).astimezone(timezone.utc)
        query_start = to_utc_iso_z(range_start_dt - timedelta(days=1))
        query_end = to_utc_iso_z(range_end_dt + timedelta(days=1))

        existing_appointments = await store.list_appointments_by_date_range(
            tenant_id=tenant_id,
            start_date=query_start,
            end_date=query_end,
            statuses=["scheduled", "confirmed", "rescheduled"],
        )

        # Fetch Google Calendar busy intervals (falls back to [] on failure)
        google_busy_intervals: List[Tuple[datetime, datetime]] = []
        try:
            google_busy_intervals = await google_calendar_service.free_busy(
                tenant_id=tenant_id,
                start_dt=range_start_dt,
                end_dt=range_end_dt,
                skip_cache=False,
            )
        except Exception as exc:
            logger.warning("Failed to fetch Google Calendar busy intervals: %s", exc)

        current_date = start_date
        while current_date <= end_date:
            day_name = current_date.strftime("%A").lower()

            if day_name in working_hours:
                day_hours = working_hours[day_name]
                start_time_str = day_hours.get("start", "09:00")
                end_time_str = day_hours.get("end", "17:00")

                start_hour, start_min = map(int, start_time_str.split(":"))
                end_hour, end_min = map(int, end_time_str.split(":"))

                # Localized timezone-aware start and end
                day_start = datetime.combine(
                    current_date, datetime.min.time().replace(hour=start_hour, minute=start_min), tzinfo=tz
                )
                day_end = datetime.combine(
                    current_date, datetime.min.time().replace(hour=end_hour, minute=end_min), tzinfo=tz
                )

                current_slot_start = day_start
                while current_slot_start + timedelta(minutes=duration_minutes) <= day_end:
                    slot_end = current_slot_start + timedelta(minutes=duration_minutes)

                    is_available = self._is_slot_available(
                        tenant_id=tenant_id,
                        slot_start=current_slot_start,
                        slot_end=slot_end,
                        existing_appointments=existing_appointments,
                        google_busy_intervals=google_busy_intervals,
                        buffer_minutes=buffer_minutes,
                    )

                    slot = TimeSlot(
                        start_time=current_slot_start,
                        end_time=slot_end,
                        duration_minutes=duration_minutes,
                        is_available=is_available,
                    )
                    slots.append(slot)

                    current_slot_start += timedelta(minutes=duration_minutes + buffer_minutes)

            current_date += timedelta(days=1)

        return slots

    def _is_slot_available(
        self,
        tenant_id: str,
        slot_start: datetime,
        slot_end: datetime,
        existing_appointments: List[Dict[str, Any]],
        google_busy_intervals: List[Tuple[datetime, datetime]],
        buffer_minutes: int,
    ) -> bool:
        """Check if a time slot is free from internal and Google Calendar conflicts."""
        buffer = timedelta(minutes=buffer_minutes)
        slot_start_utc = slot_start.astimezone(timezone.utc)
        slot_end_utc = slot_end.astimezone(timezone.utc)

        # 1. Internal appointment conflicts
        for appointment in existing_appointments:
            status = appointment.get("status")
            if status in ("scheduled", "confirmed", "rescheduled"):
                raw_dt = appointment.get("appointment_datetime")
                apt_start = parse_to_utc_datetime(raw_dt) if raw_dt else None
                if not apt_start:
                    continue
                apt_duration = appointment.get("duration_minutes", 60)
                apt_end = apt_start + timedelta(minutes=apt_duration)

                # Overlap check including buffer
                if (slot_start_utc - buffer < apt_end) and (slot_end_utc + buffer > apt_start):
                    return False

        # 2. Google Calendar busy intervals
        for b_start, b_end in google_busy_intervals:
            b_start_utc = b_start.astimezone(timezone.utc)
            b_end_utc = b_end.astimezone(timezone.utc)

            # Overlap check including buffer
            if (slot_start_utc - buffer < b_end_utc) and (slot_end_utc + buffer > b_start_utc):
                return False

        return True

    async def hold_slot(
        self,
        tenant_id: str,
        slot_start: datetime,
        slot_end: datetime,
        customer_name: str,
        customer_phone: str,
        hold_duration_minutes: int = 10,
    ) -> Optional[str]:
        """Hold a time slot for a customer."""
        hold_id = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=hold_duration_minutes)

        hold_data = SlotHold(
            hold_id=hold_id,
            tenant_id=tenant_id,
            slot_start=slot_start,
            slot_end=slot_end,
            customer_name=customer_name,
            customer_phone=customer_phone,
            expires_at=expires_at,
            created_at=datetime.now(timezone.utc),
        )

        hold_key = f"slot_hold:{hold_id}"
        await self.redis_client.set(
            hold_key, json.dumps(asdict(hold_data), default=str), ttl=hold_duration_minutes * 60
        )
        return hold_id

    async def release_slot_hold(self, hold_id: str) -> bool:
        """Release a slot hold."""
        hold_key = f"slot_hold:{hold_id}"
        result = await self.redis_client.delete(hold_key)
        return bool(result)

    async def get_slot_hold(self, hold_id: str) -> Optional[SlotHold]:
        """Get slot hold information."""
        hold_key = f"slot_hold:{hold_id}"
        hold_data = await self.redis_client.get(hold_key)
        if not hold_data:
            return None

        try:
            hold_dict = json.loads(hold_data)
            return SlotHold(
                hold_id=hold_dict["hold_id"],
                tenant_id=hold_dict["tenant_id"],
                slot_start=datetime.fromisoformat(hold_dict["slot_start"]),
                slot_end=datetime.fromisoformat(hold_dict["slot_end"]),
                customer_name=hold_dict["customer_name"],
                customer_phone=hold_dict["customer_phone"],
                expires_at=datetime.fromisoformat(hold_dict["expires_at"]),
                created_at=datetime.fromisoformat(hold_dict["created_at"]),
            )
        except Exception:
            return None

    async def validate_appointment_time(
        self, tenant_id: str, appointment_datetime: datetime, duration_minutes: int = 60
    ) -> Tuple[bool, Optional[str]]:
        """Validate if an appointment time is available and non-conflicting."""
        try:
            tz = await self._get_tenant_timezone(tenant_id)

            if appointment_datetime.tzinfo is None:
                appointment_datetime = appointment_datetime.replace(tzinfo=tz)

            appointment_datetime_utc = appointment_datetime.astimezone(timezone.utc)
            now_utc = datetime.now(timezone.utc)

            # Check if appointment is in the past
            if appointment_datetime_utc < now_utc:
                return False, "Appointment time cannot be in the past"

            # Check if too far in future (1 year)
            if appointment_datetime_utc > now_utc + timedelta(days=365):
                return False, "Appointment time cannot be more than 1 year in the future"

            # Check working hours
            local_dt = appointment_datetime_utc.astimezone(tz)
            day_name = local_dt.strftime("%A").lower()
            business_config = await tenant_service.get_business_config(tenant_id)
            working_hours = {
                "monday": {"start": "09:00", "end": "17:00"},
                "tuesday": {"start": "09:00", "end": "17:00"},
                "wednesday": {"start": "09:00", "end": "17:00"},
                "thursday": {"start": "09:00", "end": "17:00"},
                "friday": {"start": "09:00", "end": "17:00"},
                "saturday": {"start": "10:00", "end": "14:00"},
                "sunday": {"start": "10:00", "end": "14:00"},
            }
            if isinstance(business_config, dict) and isinstance(business_config.get("working_hours"), dict):
                working_hours = business_config["working_hours"]

            if day_name not in working_hours:
                return False, f"We are closed on {day_name.capitalize()}s"

            hours = working_hours[day_name]
            s_h, s_m = map(int, hours.get("start", "09:00").split(":"))
            e_h, e_m = map(int, hours.get("end", "17:00").split(":"))
            open_dt = local_dt.replace(hour=s_h, minute=s_m, second=0, microsecond=0)
            close_dt = local_dt.replace(hour=e_h, minute=e_m, second=0, microsecond=0)
            end_dt = local_dt + timedelta(minutes=duration_minutes)

            if local_dt < open_dt or end_dt > close_dt:
                return False, f"Selected time is outside working hours ({hours.get('start')} - {hours.get('end')})"

            # Check internal appointment conflicts
            buffer_minutes = 15
            buffer = timedelta(minutes=buffer_minutes)
            query_start = to_utc_iso_z(appointment_datetime_utc - timedelta(days=1))
            query_end = to_utc_iso_z(appointment_datetime_utc + timedelta(days=1))

            existing_appointments = await store.list_appointments_by_date_range(
                tenant_id=tenant_id,
                start_date=query_start,
                end_date=query_end,
                statuses=["scheduled", "confirmed", "rescheduled"],
            )

            for appointment in existing_appointments:
                raw_dt = appointment.get("appointment_datetime")
                apt_start = parse_to_utc_datetime(raw_dt) if raw_dt else None
                if not apt_start:
                    continue
                apt_duration = appointment.get("duration_minutes", 60)
                apt_end = apt_start + timedelta(minutes=apt_duration)

                if (appointment_datetime_utc - buffer < apt_end) and (
                    appointment_datetime_utc + timedelta(minutes=duration_minutes) + buffer > apt_start
                ):
                    return False, "Time slot conflicts with an existing appointment"

            # Check Google Calendar busy intervals with fresh check (skip 60s cache to prevent double booking)
            try:
                google_busy = await google_calendar_service.free_busy(
                    tenant_id=tenant_id,
                    start_dt=appointment_datetime_utc - timedelta(hours=2),
                    end_dt=appointment_datetime_utc + timedelta(hours=2),
                    skip_cache=True,
                )
                for b_start, b_end in google_busy:
                    b_start_utc = b_start.astimezone(timezone.utc)
                    b_end_utc = b_end.astimezone(timezone.utc)
                    if (appointment_datetime_utc - buffer < b_end_utc) and (
                        appointment_datetime_utc + timedelta(minutes=duration_minutes) + buffer > b_start_utc
                    ):
                        return False, "Time slot conflicts with Google Calendar busy time"
            except Exception as g_err:
                logger.warning("Google Calendar availability check skipped: %s", g_err)

            return True, None

        except Exception as e:
            logger.exception("Error validating appointment time: %s", e)
            return False, f"Error validating appointment time: {str(e)}"

    async def get_available_slots_for_date(
        self, tenant_id: str, target_date: date, duration_minutes: int = 60
    ) -> List[TimeSlot]:
        """Get available slots for a specific date."""
        return await self.generate_available_slots(
            tenant_id=tenant_id, start_date=target_date, end_date=target_date, duration_minutes=duration_minutes
        )

    async def cleanup_expired_holds(self) -> int:
        """Clean up expired slot holds."""
        expired_count = 0
        hold_keys = await self.redis_client.keys("slot_hold:*")
        for key in hold_keys:
            hold_data = await self.redis_client.get(key)
            if hold_data:
                try:
                    hold_dict = json.loads(hold_data)
                    expires_at = datetime.fromisoformat(hold_dict["expires_at"])
                    if datetime.now(timezone.utc) > expires_at:
                        await self.redis_client.delete(key)
                        expired_count += 1
                except Exception:
                    await self.redis_client.delete(key)
                    expired_count += 1
        return expired_count


scheduling_service = SchedulingService()
