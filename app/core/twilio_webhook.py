"""Shared Twilio webhook signature validation.

Validates the ``X-Twilio-Signature`` header against the tenant's auth token
(resolved from the receiving ``To`` number) or the system credentials. Used by
both the voice-agent webhooks and the SMS app webhooks so the logic lives in one
place.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import Request
from twilio.request_validator import RequestValidator

from app.core import config
from app.core.encryption import encryption_service
from app.services.store import store

logger = logging.getLogger(__name__)


async def _resolve_auth_token(form_data: Dict[str, Any]) -> str | None:
    """Find the auth token for the AccountSid in a webhook payload."""
    account_sid = form_data.get("AccountSid")
    if not account_sid:
        logger.warning("Missing AccountSid in Twilio webhook request")
        return None

    # Resolve via the receiving number (To) -> phone record -> tenant integration.
    try:
        to_number = form_data.get("To")
        if to_number:
            phone_record = await store.get_phone_by_number(to_number)
            if phone_record and phone_record.get("tenant_id"):
                integration = await store.get_twilio_integration(phone_record["tenant_id"])
                if integration and integration.get("account_sid") == account_sid:
                    encrypted_token = integration.get("auth_token")
                    if encrypted_token:
                        return encryption_service.decrypt(encrypted_token)
    except Exception as exc:
        logger.warning("Could not resolve auth_token from integration: %s", exc)

    # Fall back to system credentials.
    if config.TWILIO_ACCOUNT_SID == account_sid and config.TWILIO_AUTH_TOKEN:
        logger.info("Using system Twilio credentials for signature validation")
        return config.TWILIO_AUTH_TOKEN

    logger.error("Could not find auth_token for AccountSid: %s", account_sid)
    return None


async def validate_twilio_signature(request: Request, form_data: Dict[str, Any]) -> bool:
    """Return True if the request carries a valid Twilio signature."""
    try:
        signature = request.headers.get("X-Twilio-Signature")
        if not signature:
            logger.warning("Missing X-Twilio-Signature header in webhook request")
            return False

        auth_token = await _resolve_auth_token(form_data)
        if not auth_token:
            return False

        # Twilio validates the full URL (minus query string) + sorted POST params.
        url = str(request.url)
        if "?" in url:
            url = url.split("?")[0]
        params = {k: str(v) for k, v in form_data.items()}

        validator = RequestValidator(auth_token)
        is_valid = validator.validate(url, params, signature)
        if not is_valid:
            logger.warning("Invalid Twilio signature for AccountSid: %s", form_data.get("AccountSid"))
        return is_valid
    except Exception as exc:
        logger.error("Error validating Twilio signature: %s", exc, exc_info=True)
        return False
