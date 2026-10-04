"""Google Calendar OAuth & Sync router.

Provides endpoints for:
- Initiating Google OAuth connection (connect).
- Handling OAuth redirect callback and storing encrypted refresh token (callback).
- Fetching calendar connection status (status).
- Revoking and disconnecting Google Calendar (disconnect).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.api.v1.routers.auth import (
    get_current_user_from_token,
    require_app_access,
    verify_tenant_access,
)
from app.api.v1.services.tenant import tenant_service
from app.core.config import (
    CALENDAR_SYNC_ENABLED,
    GOOGLE_OAUTH_CLIENT_ID,
    GOOGLE_OAUTH_CLIENT_SECRET,
    GOOGLE_OAUTH_REDIRECT_URI,
    PLATFORM_APP_BASE_URL,
    SECRET_KEY,
)
from app.core.encryption import encryption_service
from app.services.google_calendar import google_calendar_service
from app.services.store import store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calendar", tags=["calendar"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"

SCOPES = [
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/calendar.events.owned",
    "https://www.googleapis.com/auth/userinfo.email",
]


def sign_oauth_state(tenant_id: str) -> str:
    """Create an HMAC-signed state token containing tenant_id and a nonce."""
    nonce = uuid.uuid4().hex
    timestamp = int(time.time())
    payload = f"{tenant_id}:{nonce}:{timestamp}"
    signature = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    raw = f"{payload}:{signature}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def verify_oauth_state(state_token: str, max_age_seconds: int = 900) -> str:
    """Verify HMAC signature and freshness of OAuth state token, returning tenant_id."""
    try:
        decoded = base64.urlsafe_b64decode(state_token.encode()).decode()
        parts = decoded.split(":")
        if len(parts) != 4:
            raise ValueError("Malformed state token")
        tenant_id, nonce, timestamp_str, signature = parts
        payload = f"{tenant_id}:{nonce}:{timestamp_str}"
        expected_sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            raise ValueError("State signature verification failed")
        timestamp = int(timestamp_str)
        if time.time() - timestamp > max_age_seconds:
            raise ValueError("State token expired")
        return tenant_id
    except Exception as exc:
        raise ValueError(f"State verification failed: {exc}") from exc


@router.get("/google/connect")
async def connect_google_calendar(
    tenant_id: str = Query(..., description="Tenant ID to connect calendar for"),
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """Return Google OAuth URL for tenant calendar authorization."""
    verify_tenant_access(current_user, tenant_id)

    if not GOOGLE_OAUTH_CLIENT_ID or not GOOGLE_OAUTH_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google OAuth credentials (GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET) are not configured.",
        )

    redirect_uri = GOOGLE_OAUTH_REDIRECT_URI
    if not redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GOOGLE_OAUTH_REDIRECT_URI is not configured.",
        )

    state = sign_oauth_state(tenant_id)
    scope_str = " ".join(SCOPES)

    params = {
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope_str,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return {"auth_url": auth_url, "state": state}


@router.get("/google/callback")
async def google_calendar_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """Handle Google OAuth callback redirect, exchange authorization code, and persist connection."""
    def build_redirect_url(
        status: str,
        error_code: Optional[str] = None,
        email: Optional[str] = None,
        tenant: Optional[str] = None,
    ) -> str:
        params = {"calendar_status": status}
        if error_code:
            params["error_code"] = error_code
        if email:
            params["email"] = email
        if tenant:
            params["tenant_id"] = tenant
        return f"{PLATFORM_APP_BASE_URL}/app/appointment-setter/calendar?{urllib.parse.urlencode(params)}"

    if error:
        logger.warning("Google OAuth callback returned error: %s", error)
        error_code = "access_denied" if "access_denied" in str(error).lower() else "auth_failed"
        return RedirectResponse(url=build_redirect_url("error", error_code=error_code))

    if not code or not state:
        logger.warning("Google OAuth callback missing code or state")
        return RedirectResponse(url=build_redirect_url("error", error_code="missing_code_or_state"))

    try:
        tenant_id = verify_oauth_state(state)
    except ValueError as exc:
        logger.warning("Invalid OAuth state in callback: %s", exc)
        return RedirectResponse(url=build_redirect_url("error", error_code="invalid_state"))

    # Exchange code for tokens with Google
    token_payload = {
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(GOOGLE_TOKEN_URL, data=token_payload)

        if token_resp.status_code != 200:
            logger.error("Failed to exchange Google OAuth code: %s", token_resp.text)
            return RedirectResponse(url=build_redirect_url("error", error_code="token_exchange_failed"))

        token_data = token_resp.json()
        refresh_token = token_data.get("refresh_token")
        access_token = token_data.get("access_token")
        granted_scopes = token_data.get("scope", "").split()

        # If refresh token not returned (already granted previously), fallback to existing stored token
        existing_conn = await store.get_calendar_connection(tenant_id)
        if not refresh_token and existing_conn:
            refresh_token = encryption_service.decrypt(existing_conn["refresh_token_enc"])

        if not refresh_token:
            logger.error("No refresh token returned by Google for tenant %s", tenant_id)
            return RedirectResponse(url=build_redirect_url("error", error_code="no_refresh_token"))

        # Query primary calendar to get timezone and calendar metadata
        cal_timezone = "UTC"
        account_email = ""

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                cal_resp = await client.get(
                    f"{GOOGLE_CALENDAR_API}/calendars/primary",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if cal_resp.status_code == 200:
                    cal_info = cal_resp.json()
                    cal_timezone = cal_info.get("timeZone", "UTC")
                    account_email = cal_info.get("id", "")

                # Also fetch user info email if available
                userinfo_resp = await client.get(
                    "https://www.googleapis.com/oauth2/v2/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if userinfo_resp.status_code == 200:
                    u_email = userinfo_resp.json().get("email")
                    if u_email:
                        account_email = u_email
        except Exception as info_err:
            logger.warning("Error fetching calendar/userinfo metadata: %s", info_err)

        if not account_email:
            account_email = f"tenant-{tenant_id}@connected"

        # Encrypt refresh token
        encrypted_token = encryption_service.encrypt(refresh_token)

        # Upsert calendar connection in PostgreSQL
        await store.upsert_calendar_connection(
            {
                "tenant_id": tenant_id,
                "provider": "google",
                "account_email": account_email,
                "calendar_id": "primary",
                "refresh_token_enc": encrypted_token,
                "scopes": granted_scopes,
                "timezone": cal_timezone,
                "status": "active",
                "last_sync_at": datetime.now(timezone.utc),
            }
        )

        # Update tenant business configuration timezone if not already set
        try:
            biz_config = await tenant_service.get_business_config(tenant_id)
            if not biz_config or not biz_config.get("timezone"):
                new_cfg = dict(biz_config or {})
                new_cfg["timezone"] = cal_timezone
                await tenant_service.save_business_config(tenant_id, new_cfg)
        except Exception as cfg_err:
            logger.warning("Failed to update tenant business config timezone: %s", cfg_err)

        logger.info(
            "Successfully connected Google Calendar for tenant %s (account: %s, tz: %s)",
            tenant_id,
            account_email,
            cal_timezone,
        )

        return RedirectResponse(
            url=build_redirect_url("connected", email=account_email, tenant=tenant_id)
        )

    except Exception as exc:
        logger.exception("Unexpected error in google_calendar_callback: %s", exc)
        return RedirectResponse(url=build_redirect_url("error", error_code="internal_error"))


@router.get("/status")
async def get_calendar_status(
    tenant_id: str = Query(..., description="Tenant ID"),
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """Retrieve Google Calendar connection status for the specified tenant."""
    verify_tenant_access(current_user, tenant_id)

    connection = await store.get_calendar_connection(tenant_id)
    if not connection or connection.get("status") == "revoked":
        return {
            "connected": False,
            "status": "disconnected",
            "account_email": None,
            "calendar_id": None,
            "timezone": None,
            "last_sync_at": None,
        }

    return {
        "connected": connection.get("status") == "active",
        "status": connection.get("status", "disconnected"),
        "account_email": connection.get("account_email"),
        "calendar_id": connection.get("calendar_id", "primary"),
        "timezone": connection.get("timezone", "UTC"),
        "scopes": connection.get("scopes", []),
        "last_sync_at": connection.get("last_sync_at"),
        "created_at": connection.get("created_at"),
    }


@router.delete("/google")
async def disconnect_google_calendar(
    tenant_id: str = Query(..., description="Tenant ID"),
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """Revoke Google Calendar OAuth token and remove connection."""
    verify_tenant_access(current_user, tenant_id)

    await google_calendar_service.revoke_connection(tenant_id)
    logger.info("Google Calendar disconnected for tenant %s by user %s", tenant_id, current_user.get("id"))
    return {"message": "Google Calendar disconnected and token revoked successfully"}
