"""DB-integration tests for the SMS App store layer.

These require a reachable PostgreSQL (the SMS tables use JSONB + FOR UPDATE
SKIP LOCKED, which are Postgres features). They connect to ``TEST_DATABASE_URL``
(falling back to the app's ``DATABASE_URL``), create only the SMS tables, and
skip cleanly when no database is available — so they run in CI / once Postgres
is up without blocking the unit suite.

Run locally:
    docker compose up -d postgres
    python -m pytest tests/test_sms_store_integration.py -q
"""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import DATABASE_URL
from app.services.database import check_database_health
from app.services.postgres_models import (
    ScheduledSendModel,
    SmsCampaignEnrollmentModel,
    SmsCampaignModel,
    SmsConversationModel,
    SmsLeadModel,
    SmsMessageModel,
    SmsSuppressionModel,
)
from app.services.postgres_store import postgres_store as store

pytestmark = pytest.mark.skipif(
    not check_database_health(),
    reason=f"No reachable PostgreSQL at {DATABASE_URL!r}; set TEST_DATABASE_URL or start docker compose postgres.",
)

_SMS_TABLES = [
    SmsLeadModel.__table__,
    SmsCampaignModel.__table__,
    SmsCampaignEnrollmentModel.__table__,
    ScheduledSendModel.__table__,
    SmsMessageModel.__table__,
    SmsConversationModel.__table__,
    SmsSuppressionModel.__table__,
]


@pytest.fixture(scope="module", autouse=True)
def _sms_schema():
    """Create the SMS tables for the test run (idempotent), leave them in place."""
    from app.services.database import get_engine

    engine = get_engine()
    for table in _SMS_TABLES:
        table.create(bind=engine, checkfirst=True)
    yield


def _run(coro):
    return asyncio.run(coro)


def test_lead_upsert_is_idempotent_on_phone():
    tenant = f"t-{uuid.uuid4()}"
    phone = "+14155550001"
    first = _run(store.upsert_sms_lead({"id": str(uuid.uuid4()), "tenant_id": tenant, "phone_number": phone, "name": "A"}))
    second = _run(store.upsert_sms_lead({"id": str(uuid.uuid4()), "tenant_id": tenant, "phone_number": phone, "name": "B"}))
    # Same row (same id), updated name — not a duplicate.
    assert first["id"] == second["id"]
    assert second["name"] == "B"
    leads = _run(store.list_sms_leads(tenant))
    assert len([leadv for leadv in leads if leadv["phone_number"] == phone]) == 1


def test_scheduled_send_unique_per_step():
    tenant = f"t-{uuid.uuid4()}"
    enrollment_id = str(uuid.uuid4())
    base = {
        "tenant_id": tenant,
        "campaign_id": "c1",
        "enrollment_id": enrollment_id,
        "lead_id": "l1",
        "to_phone_number": "+14155550002",
        "step_index": 0,
        "send_after": datetime.now(timezone.utc),
        "body": "hi",
    }
    a = _run(store.create_scheduled_send({**base, "id": str(uuid.uuid4())}))
    b = _run(store.create_scheduled_send({**base, "id": str(uuid.uuid4())}))
    assert a is not None
    assert b is None  # duplicate (enrollment_id, step_index) → idempotent no-op


def test_message_unique_on_twilio_sid():
    tenant = f"t-{uuid.uuid4()}"
    sid = f"SM{uuid.uuid4().hex}"
    m1 = _run(store.create_sms_message({"id": str(uuid.uuid4()), "tenant_id": tenant, "direction": "outbound", "twilio_sid": sid, "status": "sent"}))
    m2 = _run(store.create_sms_message({"id": str(uuid.uuid4()), "tenant_id": tenant, "direction": "outbound", "twilio_sid": sid, "status": "sent"}))
    assert m1 is not None
    assert m2 is None  # duplicate SID → idempotent (webhook replay safety)


def test_suppression_blocks_and_is_idempotent():
    tenant = f"t-{uuid.uuid4()}"
    phone = "+14155550003"
    assert _run(store.is_sms_suppressed(tenant, phone)) is False
    _run(store.add_sms_suppression({"id": str(uuid.uuid4()), "tenant_id": tenant, "phone_number": phone, "reason": "opted_out"}))
    _run(store.add_sms_suppression({"id": str(uuid.uuid4()), "tenant_id": tenant, "phone_number": phone, "reason": "opted_out"}))
    assert _run(store.is_sms_suppressed(tenant, phone)) is True
    assert len(_run(store.list_sms_suppressions(tenant))) == 1


def test_claim_due_sends_marks_claimed_and_skips_future():
    tenant = f"t-{uuid.uuid4()}"
    enrollment_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    due = _run(store.create_scheduled_send({
        "id": str(uuid.uuid4()), "tenant_id": tenant, "campaign_id": "c", "enrollment_id": enrollment_id,
        "lead_id": "l", "to_phone_number": "+14155550004", "step_index": 0,
        "send_after": now - timedelta(minutes=1), "body": "due",
    }))
    future = _run(store.create_scheduled_send({
        "id": str(uuid.uuid4()), "tenant_id": tenant, "campaign_id": "c", "enrollment_id": enrollment_id,
        "lead_id": "l", "to_phone_number": "+14155550004", "step_index": 1,
        "send_after": now + timedelta(hours=1), "body": "future",
    }))
    claimed = _run(store.claim_due_scheduled_sends(limit=10))
    claimed_ids = {c["id"] for c in claimed}
    assert due["id"] in claimed_ids
    assert future["id"] not in claimed_ids
    # A second claim does not re-claim the now-"claimed" row.
    again = _run(store.claim_due_scheduled_sends(limit=10))
    assert due["id"] not in {c["id"] for c in again}


def test_cancel_pending_sends_for_lead():
    tenant = f"t-{uuid.uuid4()}"
    lead_id = str(uuid.uuid4())
    enrollment_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    _run(store.create_scheduled_send({
        "id": str(uuid.uuid4()), "tenant_id": tenant, "campaign_id": "c", "enrollment_id": enrollment_id,
        "lead_id": lead_id, "to_phone_number": "+14155550005", "step_index": 0,
        "send_after": now + timedelta(hours=2), "body": "x",
    }))
    canceled = _run(store.cancel_pending_sends_for_lead(tenant, lead_id))
    assert canceled == 1


def test_conversation_thread_is_unique():
    tenant = f"t-{uuid.uuid4()}"
    payload = {
        "id": str(uuid.uuid4()), "tenant_id": tenant, "lead_id": "l1",
        "lead_phone_number": "+14155550006", "tenant_phone_number": "+14155550100",
    }
    a = _run(store.upsert_sms_conversation(payload))
    b = _run(store.upsert_sms_conversation({**payload, "id": str(uuid.uuid4())}))
    assert a["id"] == b["id"]  # same thread, not a duplicate
