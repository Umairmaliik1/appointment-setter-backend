"""Unit tests for SMS App pure logic: template rendering, quiet-hours scheduling,
and STOP/START keyword detection. These do not touch the database."""

from datetime import datetime, timezone

from app.api.v1.services.sms import (
    SmsCampaignService,
    SmsWebhookService,
    render_template,
)


def test_render_template_fills_known_tokens():
    lead = {"name": "Alice", "phone_number": "+14155550123", "merge_fields": {"company": "Acme"}}
    body = "Hi {{name}} from {{company}} — reply to chat. ({{phone_number}})"
    assert render_template(body, lead) == "Hi Alice from Acme — reply to chat. (+14155550123)"


def test_render_template_unknown_token_is_blank():
    lead = {"name": "Bob", "merge_fields": {}}
    assert render_template("Hi {{name}}{{missing}}!", lead) == "Hi Bob!"


def test_render_template_handles_no_name():
    lead = {"merge_fields": {}}
    assert render_template("Hello {{name}}", lead) == "Hello "


def test_quiet_hours_defers_into_allowed_window():
    svc = SmsCampaignService()
    campaign = {"timezone": "UTC", "respect_quiet_hours": True}
    # 23:00 UTC falls inside the default quiet window (21 -> 9), should push to 09:00 next day.
    base = datetime(2026, 6, 29, 23, 0, tzinfo=timezone.utc)
    out = svc._next_send_time(base, delay_minutes=0, campaign=campaign)
    assert out.hour == 9
    assert out.date() > base.date()


def test_quiet_hours_allows_daytime_send():
    svc = SmsCampaignService()
    campaign = {"timezone": "UTC", "respect_quiet_hours": True}
    base = datetime(2026, 6, 29, 14, 0, tzinfo=timezone.utc)  # 2pm is allowed
    out = svc._next_send_time(base, delay_minutes=0, campaign=campaign)
    assert out == base


def test_quiet_hours_can_be_disabled():
    svc = SmsCampaignService()
    campaign = {"timezone": "UTC", "respect_quiet_hours": False}
    base = datetime(2026, 6, 29, 23, 30, tzinfo=timezone.utc)
    out = svc._next_send_time(base, delay_minutes=30, campaign=campaign)
    assert out.hour == 0  # 23:30 + 30m, no quiet-hours deferral


def test_stop_keyword_detection():
    svc = SmsWebhookService()
    assert svc._is_stop("STOP") is True
    assert svc._is_stop("  stop  ") is True
    assert svc._is_stop("UNSUBSCRIBE") is True
    assert svc._is_stop("hello there") is False


def test_start_keyword_detection():
    svc = SmsWebhookService()
    assert svc._is_start("START") is True
    assert svc._is_start("yes") is True
    assert svc._is_start("STOP") is False
