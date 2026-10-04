"""
Automated Pytest Suite for Google Calendar Sync (v1 spec)
Tests:
- Datetime normalization to canonical UTC ISO with Z
- Busy intervals and buffers slot subtraction
- DST (Daylight Saving Time) handling
- Token refresh & invalid_grant -> needs_reauth
- Idempotent create (409 treated as success)
- Google down fallback (booking succeeds, status pending/retry)
- Reschedule (PATCH) and Cancel (DELETE with 404/410 handling)
- check_availability output format & 3-slot limit
- Tenant isolation
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.core.datetime_utils import parse_to_utc_datetime, to_utc_iso_z
from app.services.google_calendar import (
    GoogleCalendarService,
    generate_google_event_id,
)
from app.api.v1.services.scheduling import SchedulingService, TimeSlot
from app.api.v1.routers.calendar import sign_oauth_state, verify_oauth_state
from app.api.v1.routers.auth import verify_tenant_access
from fastapi import HTTPException


# ============================================================================
# 1. Datetime Normalization & UTC ISO with Z
# ============================================================================

def test_datetime_normalization_utc_z():
    """Verify all date/times are strictly normalized to canonical UTC ISO with 'Z'."""
    # 1. Naive string (assumed UTC)
    assert to_utc_iso_z("2026-10-15T14:30:00") == "2026-10-15T14:30:00Z"

    # 2. Offset string (-05:00 America/Chicago)
    # 09:30 -05:00 is 14:30 UTC
    assert to_utc_iso_z("2026-10-15T09:30:00-05:00") == "2026-10-15T14:30:00Z"

    # 3. +02:00 offset
    # 16:30 +02:00 is 14:30 UTC
    assert to_utc_iso_z("2026-10-15T16:30:00+02:00") == "2026-10-15T14:30:00Z"

    # 4. Datetime object with tzinfo
    tz_chicago = ZoneInfo("America/Chicago")
    dt = datetime(2026, 10, 15, 9, 30, tzinfo=tz_chicago)
    assert to_utc_iso_z(dt) == "2026-10-15T14:30:00Z"

    # 5. String lexicographical sorting consistency:
    # Multiple representations of the same or ordered times must sort correctly as strings
    times = [
        "2026-10-15T18:00:00+02:00",  # 16:00Z
        "2026-10-15T09:00:00-05:00",  # 14:00Z
        "2026-10-15T15:00:00Z",       # 15:00Z
    ]
    normalized = [to_utc_iso_z(t) for t in times]
    sorted_normalized = sorted(normalized)
    assert sorted_normalized == [
        "2026-10-15T14:00:00Z",
        "2026-10-15T15:00:00Z",
        "2026-10-15T16:00:00Z",
    ]


# ============================================================================
# 2. Busy Intervals and Buffers Remove the Right Slots
# ============================================================================

def test_busy_intervals_and_buffers_remove_slots():
    """Verify slots overlapping internal or Google Calendar busy intervals + buffers are removed."""
    scheduling = SchedulingService()
    tz = ZoneInfo("America/Chicago")

    # Slot: 10:00 AM - 11:00 AM Central
    slot_date = date(2026, 10, 15)
    slot_start = datetime(2026, 10, 15, 10, 0, tzinfo=tz)
    slot_end = datetime(2026, 10, 15, 11, 0, tzinfo=tz)

    # 1. No conflicts -> Available
    assert scheduling._is_slot_available(
        tenant_id="tenant-1",
        slot_start=slot_start,
        slot_end=slot_end,
        existing_appointments=[],
        google_busy_intervals=[],
        buffer_minutes=15,
    ) is True

    # 2. Google Calendar busy interval at 10:30 - 11:30 Central (direct overlap)
    google_busy = [
        (
            datetime(2026, 10, 15, 10, 30, tzinfo=tz).astimezone(timezone.utc),
            datetime(2026, 10, 15, 11, 30, tzinfo=tz).astimezone(timezone.utc),
        )
    ]
    assert scheduling._is_slot_available(
        tenant_id="tenant-1",
        slot_start=slot_start,
        slot_end=slot_end,
        existing_appointments=[],
        google_busy_intervals=google_busy,
        buffer_minutes=15,
    ) is False

    # 3. Google Calendar busy interval ending at 09:50 Central (10 minutes before slot start)
    # With 15-minute buffer, slot start (10:00) minus 15 min is 09:45, which overlaps 09:50!
    buffer_conflict_busy = [
        (
            datetime(2026, 10, 15, 9, 0, tzinfo=tz).astimezone(timezone.utc),
            datetime(2026, 10, 15, 9, 50, tzinfo=tz).astimezone(timezone.utc),
        )
    ]
    assert scheduling._is_slot_available(
        tenant_id="tenant-1",
        slot_start=slot_start,
        slot_end=slot_end,
        existing_appointments=[],
        google_busy_intervals=buffer_conflict_busy,
        buffer_minutes=15,
    ) is False

    # 4. Google Calendar busy interval ending at 09:40 Central (20 minutes before slot start)
    # Outside the 15-minute buffer -> Available!
    outside_buffer_busy = [
        (
            datetime(2026, 10, 15, 9, 0, tzinfo=tz).astimezone(timezone.utc),
            datetime(2026, 10, 15, 9, 40, tzinfo=tz).astimezone(timezone.utc),
        )
    ]
    assert scheduling._is_slot_available(
        tenant_id="tenant-1",
        slot_start=slot_start,
        slot_end=slot_end,
        existing_appointments=[],
        google_busy_intervals=outside_buffer_busy,
        buffer_minutes=15,
    ) is True


# ============================================================================
# 3. DST Day Produces Correct Local Times
# ============================================================================

@pytest.mark.asyncio
async def test_dst_transition_produces_correct_local_times():
    """Verify that slot generation on DST transition days produces exact local business hours."""
    scheduling = SchedulingService()
    tz = ZoneInfo("America/New_York")

    # In 2026, US Spring Forward is Sunday, March 8, 2026
    dst_date = date(2026, 3, 8)

    with patch.object(scheduling, "_get_tenant_timezone", return_value=tz), \
         patch("app.api.v1.services.scheduling.tenant_service.get_tenant", return_value={"id": "tenant-dst"}), \
         patch("app.api.v1.services.scheduling.tenant_service.get_business_config", return_value={
             "timezone": "America/New_York",
             "working_hours": {"sunday": {"start": "10:00", "end": "14:00"}},
         }), \
         patch("app.api.v1.services.scheduling.store.list_appointments_by_date_range", return_value=[]), \
         patch("app.api.v1.services.scheduling.google_calendar_service.free_busy", return_value=[]):

        slots = await scheduling.generate_available_slots(
            tenant_id="tenant-dst",
            start_date=dst_date,
            end_date=dst_date,
            duration_minutes=60,
            buffer_minutes=0,
        )

        assert len(slots) == 4
        # Slot 1: 10:00 AM local time
        assert slots[0].start_time.hour == 10
        assert slots[0].start_time.tzinfo == tz
        # Slot 4: 1:00 PM local time
        assert slots[3].start_time.hour == 13


# ============================================================================
# 4. Token Refresh & invalid_grant Sets needs_reauth
# ============================================================================

@pytest.mark.asyncio
async def test_token_refresh_success_and_invalid_grant():
    """Test OAuth token refresh flow and invalid_grant status update."""
    # 1. Success case
    def success_handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://oauth2.googleapis.com/token"
        return httpx.Response(200, json={"access_token": "mock-access-token-123", "expires_in": 3600})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(success_handler))
    service = GoogleCalendarService(client=mock_client)
    service.redis_client = AsyncMock()
    service.redis_client.get.return_value = None

    with patch("app.services.google_calendar.store.get_calendar_connection", return_value={
        "tenant_id": "tenant-1",
        "status": "active",
        "refresh_token_enc": "enc-token",
    }), patch("app.services.google_calendar.encryption_service.decrypt", return_value="plain-refresh-token"):

        token = await service.get_access_token("tenant-1")
        assert token == "mock-access-token-123"
        service.redis_client.set.assert_called_once()

    # 2. invalid_grant case
    def invalid_grant_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})

    mock_client_err = httpx.AsyncClient(transport=httpx.MockTransport(invalid_grant_handler))
    service_err = GoogleCalendarService(client=mock_client_err)
    service_err.redis_client = AsyncMock()
    service_err.redis_client.get.return_value = None
    service_err._notify_admin_reauth_needed = AsyncMock()

    with patch("app.services.google_calendar.store.get_calendar_connection", return_value={
        "tenant_id": "tenant-1",
        "status": "active",
        "refresh_token_enc": "enc-token",
    }), patch("app.services.google_calendar.encryption_service.decrypt", return_value="plain-refresh-token"), \
         patch("app.services.google_calendar.store.update_calendar_connection_status", new_callable=AsyncMock) as mock_update_status:

        token = await service_err.get_access_token("tenant-1")
        assert token is None
        mock_update_status.assert_called_once_with("tenant-1", "needs_reauth")
        service_err._notify_admin_reauth_needed.assert_called_once_with("tenant-1")


# ============================================================================
# 5. Duplicate Create (409) Treated as Success (Idempotency)
# ============================================================================

@pytest.mark.asyncio
async def test_duplicate_create_409_treated_as_success():
    """Verify that receiving a 409 Conflict from Google Calendar API is treated as success."""
    def conflict_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": {"message": "Event already exists"}})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(conflict_handler))
    service = GoogleCalendarService(client=mock_client)
    service.get_access_token = AsyncMock(return_value="valid-token")

    appointment = {
        "id": "apt-uuid-1234-5678",
        "tenant_id": "tenant-1",
        "appointment_datetime": "2026-10-15T15:00:00Z",
        "duration_minutes": 60,
        "service_type": "HVAC Repair",
        "customer_name": "Alice Smith",
        "service_address": "123 Main St",
    }

    with patch("app.services.google_calendar.store.get_calendar_connection", return_value={
        "tenant_id": "tenant-1",
        "status": "active",
        "calendar_id": "primary",
        "timezone": "America/Chicago",
    }), patch("app.services.google_calendar.store.update_appointment", new_callable=AsyncMock) as mock_update_apt, \
         patch("app.services.google_calendar.store.update_calendar_connection_status", new_callable=AsyncMock):

        event_id = await service.create_event("tenant-1", appointment)
        expected_id = generate_google_event_id(appointment["id"])

        assert event_id == expected_id
        mock_update_apt.assert_called_once_with(
            appointment["id"],
            {"calendar_event_id": expected_id, "calendar_sync_status": "synced"},
        )


# ============================================================================
# 6. Google Down: Booking Succeeds, Status Pending, Retries Run
# ============================================================================

@pytest.mark.asyncio
async def test_google_down_booking_succeeds_status_pending():
    """Verify booking survives Google outage, marking pending and retrying without dropping the booking."""
    service = GoogleCalendarService()
    service.create_event = AsyncMock(side_effect=Exception("Google Calendar Service Unavailable 503"))
    service._notify_admin_sync_failed = AsyncMock()

    appointment = {
        "id": "apt-outage-1",
        "tenant_id": "tenant-1",
        "appointment_datetime": "2026-10-15T15:00:00Z",
    }

    with patch("app.services.google_calendar.store.update_appointment", new_callable=AsyncMock) as mock_update_apt, \
         patch("asyncio.sleep", new_callable=AsyncMock):

        result = await service.sync_appointment_event_with_retry(
            tenant_id="tenant-1", appointment=appointment, max_attempts=3
        )

        assert result is None
        # Must have set pending during attempts
        mock_update_apt.assert_any_call("apt-outage-1", {"calendar_sync_status": "pending"})
        # Final status marked failed
        mock_update_apt.assert_any_call("apt-outage-1", {"calendar_sync_status": "failed"})
        # Admin notified
        service._notify_admin_sync_failed.assert_called_once()


# ============================================================================
# 7. Reschedule Patches Event & Cancel Deletes It (404 Handled)
# ============================================================================

@pytest.mark.asyncio
async def test_reschedule_patches_and_cancel_deletes_event():
    """Verify reschedule sends PATCH and cancel sends DELETE (treating 404 as already gone)."""
    # 1. Reschedule (PATCH)
    patch_called = False

    def patch_handler(request: httpx.Request) -> httpx.Response:
        nonlocal patch_called
        if request.method == "PATCH":
            patch_called = True
            return httpx.Response(200, json={"id": "event-123", "status": "confirmed"})
        return httpx.Response(400)

    client_patch = httpx.AsyncClient(transport=httpx.MockTransport(patch_handler))
    service_patch = GoogleCalendarService(client=client_patch)
    service_patch.get_access_token = AsyncMock(return_value="valid-token")

    appointment = {
        "id": "apt-1",
        "tenant_id": "tenant-1",
        "appointment_datetime": "2026-10-16T15:00:00Z",
        "duration_minutes": 60,
    }

    with patch("app.services.google_calendar.store.get_calendar_connection", return_value={
        "tenant_id": "tenant-1",
        "status": "active",
        "calendar_id": "primary",
        "timezone": "UTC",
    }), patch("app.services.google_calendar.store.update_appointment", new_callable=AsyncMock), \
         patch("app.services.google_calendar.store.update_calendar_connection_status", new_callable=AsyncMock):

        ok = await service_patch.update_event("tenant-1", "event-123", appointment)
        assert ok is True
        assert patch_called is True

    # 2. Cancel (DELETE) with 404 response
    delete_called = False

    def delete_handler(request: httpx.Request) -> httpx.Response:
        nonlocal delete_called
        if request.method == "DELETE":
            delete_called = True
            return httpx.Response(404, json={"error": {"message": "Resource has been deleted"}})
        return httpx.Response(400)

    client_del = httpx.AsyncClient(transport=httpx.MockTransport(delete_handler))
    service_del = GoogleCalendarService(client=client_del)
    service_del.get_access_token = AsyncMock(return_value="valid-token")

    with patch("app.services.google_calendar.store.get_calendar_connection", return_value={
        "tenant_id": "tenant-1",
        "calendar_id": "primary",
    }):
        # 404 on delete must be treated as success (already gone)
        del_ok = await service_del.delete_event("tenant-1", "event-123")
        assert del_ok is True
        assert delete_called is True


# ============================================================================
# 8. check_availability Output Format and 3-Slot Limit
# ============================================================================

@pytest.mark.asyncio
async def test_check_availability_tool_format_and_limit():
    """Verify check_availability returns up to 3 slots in conversational plain words with ISO times."""
    from app.agents.voice_worker import VoiceAgent

    agent = VoiceAgent(
        instructions="test instructions",
        tenant_id="tenant-test",
    )

    tz = ZoneInfo("America/Chicago")
    # Build 5 dummy slots
    base_time = datetime(2026, 10, 15, 9, 0, tzinfo=tz)
    mock_slots = [
        TimeSlot(start_time=base_time + timedelta(hours=i), end_time=base_time + timedelta(hours=i, minutes=60), duration_minutes=60, is_available=True)
        for i in range(5)
    ]

    with patch("app.api.v1.services.scheduling.scheduling_service._get_tenant_timezone", return_value=tz), \
         patch("app.api.v1.services.scheduling.scheduling_service.generate_available_slots", return_value=mock_slots):

        result = await agent.check_availability(date="2026-10-15", part_of_day="morning")

        # Must contain "Available slots:"
        assert "Available slots:" in result
        # Check slot formatting: includes day of week, 12h time with AM/PM, and ISO string
        assert "Thursday 9:00 AM (2026-10-15T09:00:00-05:00)" in result
        assert "Thursday 10:00 AM (2026-10-15T10:00:00-05:00)" in result
        assert "Thursday 11:00 AM (2026-10-15T11:00:00-05:00)" in result
        # Must be limited to at most 3 slots
        assert "Thursday 12:00 PM" not in result


# ============================================================================
# 9. Tenant Isolation
# ============================================================================

def test_tenant_isolation_calendar_connections():
    """Verify that tenant A cannot access or control tenant B's connection."""
    # User associated only with tenant-A
    user_a = {
        "id": "user-a",
        "role": "user",
        "tenant_id": "tenant-A",
        "accessible_tenant_ids": ["tenant-A"],
    }

    # User A attempting to access tenant-A: Allowed
    verify_tenant_access(user_a, "tenant-A")

    # User A attempting to access tenant-B: Access Denied (HTTP 403)
    with pytest.raises(HTTPException) as exc_info:
        verify_tenant_access(user_a, "tenant-B")
    assert exc_info.value.status_code == 403

    # State signature verification: State generated for tenant-A cannot be used for tenant-B
    state_a = sign_oauth_state("tenant-A")
    resolved_tenant = verify_oauth_state(state_a)
    assert resolved_tenant == "tenant-A"
    assert resolved_tenant != "tenant-B"


# ============================================================================
# 10. Background Sweeper (Restart Recovery)
# ============================================================================

@pytest.mark.asyncio
async def test_calendar_sync_sweeper_restart_recovery():
    """Verify that pending appointments left after crash/restart are processed by the sweeper."""
    service = GoogleCalendarService()

    # Simulate 3 appointments left in database after a restart
    pending_appointments = [
        {
            "id": "apt-pending-1",
            "tenant_id": "tenant-active",
            "status": "confirmed",
            "appointment_datetime": "2026-10-15T15:00:00Z",
            "customer_name": "Alice Smith",
            "calendar_sync_status": "pending",
        },
        {
            "id": "apt-cancelled",
            "tenant_id": "tenant-active",
            "status": "cancelled",
            "appointment_datetime": "2026-10-15T16:00:00Z",
            "calendar_sync_status": "pending",
        },
        {
            "id": "apt-no-conn",
            "tenant_id": "tenant-no-conn",
            "status": "confirmed",
            "appointment_datetime": "2026-10-15T17:00:00Z",
            "calendar_sync_status": "pending",
        },
    ]

    mock_conn = {
        "tenant_id": "tenant-active",
        "status": "active",
        "calendar_id": "primary",
    }

    async def mock_get_conn(t_id):
        if t_id == "tenant-active":
            return mock_conn
        return None

    updated_records = {}

    async def mock_update_apt(apt_id, data):
        updated_records[apt_id] = data
        return {"id": apt_id, **data}

    with patch("app.services.google_calendar.store.list_pending_calendar_sync_appointments", AsyncMock(return_value=pending_appointments)), \
         patch("app.services.google_calendar.store.get_calendar_connection", AsyncMock(side_effect=mock_get_conn)), \
         patch("app.services.google_calendar.store.update_appointment", AsyncMock(side_effect=mock_update_apt)), \
         patch.object(service, "sync_appointment_event_with_retry", AsyncMock(return_value="gcal-event-swept-1")):

        processed = await service.sweep_pending_calendar_syncs()

        # Should have successfully synced 1 active appointment
        assert processed == 1
        service.sync_appointment_event_with_retry.assert_called_once_with(
            "tenant-active", pending_appointments[0], max_attempts=3
        )

        # Cancelled appointment marked as skipped
        assert updated_records["apt-cancelled"]["calendar_sync_status"] == "skipped"

        # Tenant with no connection marked as not_applicable (prevents infinite 60s retry loop)
        assert updated_records["apt-no-conn"]["calendar_sync_status"] == "not_applicable"

    # Also verify sweeper task cancellation cleanly terminates
    sweeper_task = asyncio.create_task(service.run_calendar_sync_sweeper(interval_seconds=0.01))
    await asyncio.sleep(0.02)
    sweeper_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sweeper_task


# ============================================================================
# 11. Fresh Booking Check (skip_cache=True) & Pending Status Saved Before Return
# ============================================================================

@pytest.mark.asyncio
async def test_fresh_google_check_and_pending_status_saved_before_return():
    """Verify:
    1. Booking step skips the 60s cache (skip_cache=True) while slots check uses cache.
    2. calendar_sync_status='pending' is persisted to database BEFORE create_appointment returns
       when tenant has active Google Calendar connection.
    3. calendar_sync_status='not_applicable' when tenant has NO Google Calendar connection.
    """
    scheduling = SchedulingService()
    tz = ZoneInfo("America/Chicago")
    apt_dt = datetime(2026, 10, 15, 14, 0, tzinfo=tz)

    mock_free_busy = AsyncMock(return_value=[])

    with patch.object(scheduling, "_get_tenant_timezone", AsyncMock(return_value=tz)), \
         patch("app.api.v1.services.scheduling.tenant_service.get_tenant", AsyncMock(return_value={"id": "tenant-test", "timezone": "America/Chicago"})), \
         patch("app.api.v1.services.scheduling.tenant_service.get_business_config", AsyncMock(return_value={
             "working_hours": {"thursday": {"start": "09:00", "end": "17:00"}}
         })), \
         patch("app.api.v1.services.scheduling.store.list_appointments_by_date_range", AsyncMock(return_value=[])), \
         patch("app.api.v1.services.scheduling.google_calendar_service.free_busy", mock_free_busy):

        # 1. Slot generation uses 60s cache (skip_cache=False)
        await scheduling.generate_available_slots(
            tenant_id="tenant-test",
            start_date=date(2026, 10, 15),
            end_date=date(2026, 10, 15),
        )
        assert mock_free_busy.call_args.kwargs.get("skip_cache") is False

        # 2. Booking validation skips 60s cache (skip_cache=True)
        is_valid, err = await scheduling.validate_appointment_time(
            tenant_id="tenant-test",
            appointment_datetime=apt_dt,
            duration_minutes=60,
        )
        assert is_valid is True
        assert mock_free_busy.call_args.kwargs.get("skip_cache") is True

    # 3. Verify calendar_sync_status='pending' when active connection exists
    from app.api.v1.services.appointment import AppointmentService
    apt_service = AppointmentService()

    saved_appointment_payload = None

    async def mock_create_apt(payload):
        nonlocal saved_appointment_payload
        saved_appointment_payload = dict(payload)
        return dict(payload)

    with patch.object(apt_service.scheduling_service, "validate_appointment_time", AsyncMock(return_value=(True, None))), \
         patch("app.api.v1.services.appointment.store.create_appointment", AsyncMock(side_effect=mock_create_apt)), \
         patch("app.api.v1.services.appointment.store.get_org_by_legacy_tenant_id", AsyncMock(return_value=None)), \
         patch("app.api.v1.services.appointment.store.get_calendar_connection", AsyncMock(return_value={"status": "active"})), \
         patch("app.api.v1.services.appointment.google_calendar_service.sync_appointment_event_with_retry", AsyncMock()):

        created = await apt_service.create_appointment(
            tenant_id="tenant-test",
            customer_name="Bob Test",
            customer_phone="+15551234567",
            customer_email="bob@test.com",
            service_type="HVAC Repair",
            service_address="123 Main St",
            appointment_datetime=apt_dt,
            send_email=False,
            sync_calendar=True,
        )

        assert created is not None
        # Assert database write received calendar_sync_status == "pending"
        assert saved_appointment_payload is not None
        assert saved_appointment_payload["calendar_sync_status"] == "pending"
        assert created["calendar_sync_status"] == "pending"

    # 4. Verify calendar_sync_status='not_applicable' when no calendar connection exists
    with patch.object(apt_service.scheduling_service, "validate_appointment_time", AsyncMock(return_value=(True, None))), \
         patch("app.api.v1.services.appointment.store.create_appointment", AsyncMock(side_effect=mock_create_apt)), \
         patch("app.api.v1.services.appointment.store.get_org_by_legacy_tenant_id", AsyncMock(return_value=None)), \
         patch("app.api.v1.services.appointment.store.get_calendar_connection", AsyncMock(return_value=None)):

        created_no_cal = await apt_service.create_appointment(
            tenant_id="tenant-no-cal",
            customer_name="Alice Test",
            customer_phone="+15559876543",
            customer_email="alice@test.com",
            service_type="Plumbing",
            service_address="456 Elm St",
            appointment_datetime=apt_dt,
            send_email=False,
            sync_calendar=True,
        )

        assert created_no_cal is not None
        assert saved_appointment_payload["calendar_sync_status"] == "not_applicable"
        assert created_no_cal["calendar_sync_status"] == "not_applicable"
