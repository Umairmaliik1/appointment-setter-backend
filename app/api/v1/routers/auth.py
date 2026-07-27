"""
Authentication API routes using PostgreSQL.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from jose import JWTError, jwt

from app.api.v1.schemas.auth import (
    ForgotPasswordRequest,
    LogoutRequest,
    PasswordChange,
    RefreshTokenRequest,
    ResetPasswordRequest,
    SetupPasswordConfirmRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
    UserUpdate,
)
from app.api.v1.services.auth import auth_service
from app.core.config import (
    ACCESS_TOKEN_COOKIE_NAME,
    AUTH_COOKIE_DOMAIN,
    AUTH_COOKIE_SAMESITE,
    AUTH_COOKIE_SECURE,
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_REFRESH_TOKEN_EXPIRE_DAYS,
    PLATFORM_APP_BASE_URL,
    REFRESH_TOKEN_COOKIE_NAME,
    SECRET_KEY,
)
from app.core.platform_apps import has_app_access as user_has_app_access
from app.core.security import SecurityService
from app.services.email.service import email_service
from app.services.org_service import org_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["authentication"])


def _is_org_status_blocked(status_value: Optional[str]) -> bool:
    return str(status_value or "").strip().lower() in {"inactive", "suspended"}


async def _get_access_context_or_reject(user: Dict[str, Any]):
    """
    Resolve org access context and enforce org lifecycle login/session policy.

    Platform admins/staff bypass org status checks.
    Non-platform users are blocked only when all assigned orgs are suspended/inactive.
    If the stored active org is suspended/inactive, we auto-fallback to the first
    accessible active org and persist that active context.
    """
    access_context = await org_service.get_access_context(user)
    if access_context.platform_scope:
        return access_context

    accessible_orgs = access_context.accessible_orgs or []
    if not accessible_orgs:
        # Backward-compatible fallback for legacy users without org memberships.
        return access_context

    active_orgs = [org for org in accessible_orgs if not _is_org_status_blocked(org.get("status"))]
    if not active_orgs:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="All assigned organizations are inactive or suspended.",
        )

    active_org = access_context.active_org
    if active_org and not _is_org_status_blocked(active_org.get("status")):
        return access_context

    fallback_org = active_orgs[0]
    fallback_org_id = str(fallback_org.get("id", "")).strip()
    if not fallback_org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active organization is available for this account.",
        )

    user_id = str(user.get("id", "")).strip()
    if user_id:
        try:
            await auth_service.update_user_fields(user_id, {"active_org_id": fallback_org_id})
        except Exception as exc:
            logger.warning("Failed to persist fallback active org for user %s: %s", user_id, exc)

    user_with_fallback = dict(user)
    user_with_fallback["active_org_id"] = fallback_org_id
    user_with_fallback["org_memberships"] = access_context.memberships
    user_with_fallback["accessible_orgs"] = accessible_orgs
    return await org_service.get_access_context(user_with_fallback)


def _cookie_domain_or_none() -> Optional[str]:
    return AUTH_COOKIE_DOMAIN.strip() or None


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    access_max_age = JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    refresh_max_age = JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60

    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        max_age=access_max_age,
        domain=_cookie_domain_or_none(),
        path="/",
    )
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        max_age=refresh_max_age,
        domain=_cookie_domain_or_none(),
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME,
        domain=_cookie_domain_or_none(),
        path="/",
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
    )
    response.delete_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        domain=_cookie_domain_or_none(),
        path="/",
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
    )


def get_security_service() -> SecurityService:
    """Get security service instance."""
    try:
        return SecurityService()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Security service unavailable: {str(e)}")


async def get_current_user_from_token(
    request: Request, authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:
    """Get current user from JWT token."""
    try:
        token: Optional[str] = None
        if authorization:
            token = authorization[7:] if authorization.startswith("Bearer ") else authorization
        if not token and request is not None:
            token = request.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Decode JWT token
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get user from database
    user = await auth_service.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_context = await _get_access_context_or_reject(user)
    enriched_user = dict(user)
    enriched_user["org_memberships"] = access_context.memberships
    enriched_user["accessible_orgs"] = access_context.accessible_orgs
    enriched_user["accessible_tenant_ids"] = access_context.accessible_tenant_ids
    enriched_user["platform_scope"] = access_context.platform_scope
    if access_context.active_org:
        enriched_user["active_org"] = access_context.active_org
        enriched_user["active_org_id"] = access_context.active_org.get("id")
    return enriched_user


def verify_tenant_access(user: Dict[str, Any], tenant_id: str) -> None:
    """
    Verify that the authenticated user has access to the specified tenant.

    Tenant Binding Model:
    - Platform staff (`platform_scope` or `org_service.is_platform_staff`) can access ANY tenant.
    - Everyone else — INCLUDING tenant-level `role == "admin"` users — must
      have the tenant in their `accessible_tenant_ids` (driven by customer-org
      membership). A tenant admin is the admin of THEIR tenant, not of every
      other customer's tenant.
    - Legacy back-compat: users not yet migrated to org memberships fall
      through to their single `user.tenant_id` field.

    Raises HTTPException if access is denied.
    """
    user_tenant_id = user.get("tenant_id")
    accessible_tenant_ids = {str(item) for item in (user.get("accessible_tenant_ids") or []) if item}
    memberships = user.get("org_memberships") or []

    if user.get("platform_scope") or org_service.is_platform_staff(memberships):
        return

    if tenant_id in accessible_tenant_ids:
        return

    # Backward compatibility for users not yet migrated to org memberships.
    if user_tenant_id and str(user_tenant_id) == str(tenant_id):
        return

    if not user_tenant_id and not accessible_tenant_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: User is not associated with any tenant scope",
        )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Access denied: You do not have permission to access tenant {tenant_id}",
    )


def require_admin_role(user: Dict[str, Any]) -> None:
    """
    Verify that the authenticated user has admin role.

    Raises HTTPException if user is not an admin.
    """
    user_role = str(user.get("role", "user")).lower()
    memberships = user.get("org_memberships") or []
    is_platform_staff = org_service.is_platform_staff(memberships)
    if user_role != "admin" and not is_platform_staff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Platform admin/staff role required",
        )


def _is_platform_owner(user: Dict[str, Any]) -> bool:
    memberships = user.get("org_memberships") or []
    return any(str(item.get("role", "")).strip().lower() == "platform_owner" for item in memberships)


async def require_platform_permissions(user: Dict[str, Any], required_permissions: List[str]) -> None:
    """
    Enforce fine-grained platform permission checks for platform staff.

    Global admins and platform owners bypass this check.
    """
    if str(user.get("role", "")).strip().lower() == "admin" or _is_platform_owner(user):
        return
    memberships = user.get("org_memberships") or []
    if not org_service.is_platform_staff(memberships):
        return

    granted = set(await auth_service.get_user_permissions(str(user.get("id", ""))))
    if "admin:all" in granted:
        return
    if not any(permission in granted for permission in required_permissions):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient platform permissions")


def enforce_app_access(user: Dict[str, Any], app_id: str) -> None:
    if not user_has_app_access(user, app_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: {app_id} is not assigned to this account",
        )


def require_app_access(app_id: str):
    async def dependency(current_user: Dict[str, Any] = Depends(get_current_user_from_token)) -> Dict[str, Any]:
        enforce_app_access(current_user, app_id)
        return current_user

    return dependency


def get_client_ip(request: Request) -> str:
    """
    Get client IP address from request.
    Checks X-Forwarded-For header (for proxies) and falls back to direct client.
    """
    # Check for forwarded IP (when behind proxy/load balancer)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For can contain multiple IPs, take the first one
        return forwarded_for.split(",")[0].strip()

    # Check X-Real-IP header (common in Nginx)
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    # Fallback to direct client IP
    if request.client:
        return request.client.host

    return "unknown"


@router.post("/register", response_model=UserResponse)
async def register_user(user_data: UserCreate, request: Request):
    """Register a new user."""
    # Rate limiting: 10 requests per minute per IP
    security_service = get_security_service()
    client_ip = get_client_ip(request)
    await security_service.enforce_rate_limit(client_ip, limit=10, window_seconds=60, operation="auth_register")

    try:
        # Check if user already exists
        existing_user = await auth_service.get_user_by_email(user_data.email)
        if existing_user:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

        # Create user
        user = await auth_service.create_user(user_data)

        from app.core.response_mappers import to_user_response

        return to_user_response(user)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Registration failed: {str(e)}")


@router.post("/login", response_model=TokenResponse)
async def login_user(login_data: UserLogin, request: Request, response: Response):
    """Authenticate user and return tokens."""
    # Rate limiting: 10 requests per minute per IP
    security_service = get_security_service()
    client_ip = get_client_ip(request)
    await security_service.enforce_rate_limit(client_ip, limit=10, window_seconds=60, operation="auth_login")

    try:
        # Log the login attempt
        import logging

        logger = logging.getLogger(__name__)
        logger.info(f"Login attempt for email: {login_data.email}")

        # Authenticate user
        user = await auth_service.authenticate_user(login_data.email, login_data.password)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        await _get_access_context_or_reject(user)

        # Create tokens
        access_token = auth_service.create_access_token({"sub": user.get("id", "")})
        refresh_token = auth_service.create_refresh_token({"sub": user.get("id", "")})

        # Create user session
        await auth_service.create_user_session(user.get("id", ""), refresh_token)
        _set_auth_cookies(response, access_token, refresh_token)

        from app.core.response_mappers import to_user_response

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=1800,  # 30 minutes
            user=to_user_response(user),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Login failed for email %s", login_data.email)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Login failed: {str(e)}")


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    response: Response,
    refresh_data: Optional[RefreshTokenRequest] = None,
):
    """
    Refresh access token using refresh token.

    Uses session lookup from Redis to validate the refresh token.
    If session is valid, creates new tokens and updates the session.
    """
    # Rate limiting: 10 requests per minute per IP
    security_service = get_security_service()
    client_ip = get_client_ip(request)
    await security_service.enforce_rate_limit(client_ip, limit=10, window_seconds=60, operation="auth_refresh")

    try:
        presented_refresh_token = (
            refresh_data.refresh_token
            if refresh_data and refresh_data.refresh_token
            else request.cookies.get(REFRESH_TOKEN_COOKIE_NAME)
        )
        if not presented_refresh_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token missing")

        # Get user session from Redis (validates session is active and not expired)
        session = await auth_service.get_user_session(presented_refresh_token)
        if not session:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

        user_id = session.get("user_id")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session data")

        # Get user
        user = await auth_service.get_user(user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        await _get_access_context_or_reject(user)

        # Revoke old session
        await auth_service.revoke_user_session(presented_refresh_token)

        # Create new tokens
        access_token = auth_service.create_access_token({"sub": user_id})
        new_refresh_token = auth_service.create_refresh_token({"sub": user_id})

        # Create new session with new refresh token
        await auth_service.create_user_session(user_id, new_refresh_token)
        _set_auth_cookies(response, access_token, new_refresh_token)

        from app.core.response_mappers import to_user_response

        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh_token,
            token_type="bearer",
            expires_in=1800,  # 30 minutes
            user=to_user_response(user),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Token refresh failed: {str(e)}")


@router.post("/logout")
async def logout_user(request: Request, response: Response, logout_data: Optional[LogoutRequest] = None):
    """
    Logout user and invalidate refresh token.

    Revokes the session in Redis, making the refresh token unusable.
    """
    try:
        refresh_token = (
            logout_data.refresh_token
            if logout_data and logout_data.refresh_token
            else request.cookies.get(REFRESH_TOKEN_COOKIE_NAME)
        )
        if not refresh_token:
            _clear_auth_cookies(response)
            return {"message": "Successfully logged out"}

        # Revoke refresh token (deletes session from Redis)
        success = await auth_service.revoke_user_session(refresh_token)
        if not success:
            logger.warning("Failed to revoke session for refresh token (may already be revoked)")
        _clear_auth_cookies(response)

        return {"message": "Successfully logged out"}

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Logout failed: {str(e)}")


@router.get("/me", response_model=UserResponse)
async def get_current_user(current_user: Dict[str, Any] = Depends(get_current_user_from_token)):
    """Get current user information."""
    try:
        from app.core.response_mappers import to_user_response

        return to_user_response(current_user)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get user information: {str(e)}"
        )


@router.put("/change-password")
async def change_password(password_data: PasswordChange, request: Request):
    """Change user password."""
    try:
        # This would be implemented with proper JWT authentication
        # For now, return a placeholder
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Password change not yet implemented")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Password change failed: {str(e)}")


@router.post("/setup-password/confirm")
async def confirm_setup_password(payload: SetupPasswordConfirmRequest, request: Request):
    """Confirm first-time password setup from onboarding invite token."""
    security_service = get_security_service()
    client_ip = get_client_ip(request)
    await security_service.enforce_rate_limit(client_ip, limit=20, window_seconds=60, operation="auth_setup_password")

    token_payload = auth_service.verify_setup_password_token(payload.token)
    if not token_payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired setup token")

    user_id = str(token_payload.get("sub", "")).strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid setup token payload")

    user = await auth_service.get_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    token_exp = token_payload.get("exp")
    consumed = await auth_service.consume_one_time_token(
        payload.token,
        token_type="setup_password",
        exp_unix=int(token_exp) if isinstance(token_exp, int) else None,
    )
    if not consumed:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Setup token already used or invalid")

    success = await auth_service.set_user_password(user_id, payload.new_password)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return {"message": "Password set successfully"}


@router.get("/setup-password/validate")
async def validate_setup_password_token(token: str = Query(..., min_length=10)):
    """Validate setup-password token before form submit."""
    token_payload = auth_service.verify_setup_password_token(token)
    if not token_payload:
        return {"valid": False, "reason": "invalid_or_expired"}

    user_id = str(token_payload.get("sub", "")).strip()
    if not user_id:
        return {"valid": False, "reason": "invalid_payload"}

    user = await auth_service.get_user(user_id)
    if not user:
        return {"valid": False, "reason": "user_not_found"}

    if await auth_service.is_one_time_token_consumed(token, token_type="setup_password"):
        return {"valid": False, "reason": "already_used"}

    return {"valid": True}


@router.post("/forgot-password")
async def forgot_password(forgot_data: ForgotPasswordRequest, request: Request):
    """Send password reset email."""
    security_service = get_security_service()
    client_ip = get_client_ip(request)
    await security_service.enforce_rate_limit(client_ip, limit=20, window_seconds=60, operation="auth_forgot_password")

    try:
        user = await auth_service.get_user_by_email(str(forgot_data.email).strip().lower())
        if user and user.get("is_active", True):
            user_id = str(user.get("id", ""))
            if user_id:
                token = auth_service.create_password_reset_token(user_id=user_id, expires_minutes=60)
                base_url = (PLATFORM_APP_BASE_URL or "").strip().rstrip("/") or "http://localhost:3000"
                reset_password_url = f"{base_url}/mindrind/admin/reset-password?token={token}"
                recipient_name = f"{str(user.get('first_name', '')).strip()} {str(user.get('last_name', '')).strip()}".strip() or "there"
                await email_service.send_password_reset_email(
                    recipient_email=str(user.get("email", "")),
                    recipient_name=recipient_name,
                    reset_password_url=reset_password_url,
                    expires_in_minutes=60,
                    platform_name="MindRind",
                )
        # Do not reveal whether an account exists for security.
        return {"message": "If your email exists, a password reset link has been sent."}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Password reset failed: {str(e)}")


@router.post("/reset-password")
async def reset_password(reset_data: ResetPasswordRequest, request: Request):
    """Reset user password."""
    security_service = get_security_service()
    client_ip = get_client_ip(request)
    await security_service.enforce_rate_limit(client_ip, limit=20, window_seconds=60, operation="auth_reset_password")

    token_payload = auth_service.verify_password_reset_token(reset_data.token)
    if not token_payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired reset token")

    user_id = str(token_payload.get("sub", "")).strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid reset token payload")

    user = await auth_service.get_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    token_exp = token_payload.get("exp")
    consumed = await auth_service.consume_one_time_token(
        reset_data.token,
        token_type="password_reset",
        exp_unix=int(token_exp) if isinstance(token_exp, int) else None,
    )
    if not consumed:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Reset token already used or invalid")

    success = await auth_service.set_user_password(user_id, reset_data.new_password)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return {"message": "Password reset successfully"}


@router.get("/reset-password/validate")
async def validate_reset_password_token(token: str = Query(..., min_length=10)):
    """Validate reset-password token before form submit."""
    token_payload = auth_service.verify_password_reset_token(token)
    if not token_payload:
        return {"valid": False, "reason": "invalid_or_expired"}

    user_id = str(token_payload.get("sub", "")).strip()
    if not user_id:
        return {"valid": False, "reason": "invalid_payload"}

    user = await auth_service.get_user(user_id)
    if not user:
        return {"valid": False, "reason": "user_not_found"}

    if await auth_service.is_one_time_token_consumed(token, token_type="password_reset"):
        return {"valid": False, "reason": "already_used"}

    return {"valid": True}


@router.get("/users", response_model=List[UserResponse])
async def list_users(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """List users (admin only)."""
    require_admin_role(current_user)
    try:
        require_admin_role(current_user)
        await require_platform_permissions(current_user, ["users:read", "users:write"])
        # Get users from database
        users = await auth_service.list_users(limit, offset)

        # Convert to UserResponse objects
        from app.core.response_mappers import to_user_response

        return [to_user_response(user) for user in users]

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to list users: {str(e)}")


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: str, request: Request, current_user: Dict[str, Any] = Depends(get_current_user_from_token)):
    """Get user by ID (admin only)."""
    require_admin_role(current_user)
    try:
        require_admin_role(current_user)
        await require_platform_permissions(current_user, ["users:read", "users:write"])
        user = await auth_service.get_user(user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        from app.core.response_mappers import to_user_response

        return to_user_response(user)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get user: {str(e)}")


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    user_data: UserUpdate,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user_from_token),
):
    """Update user (admin only)."""
    require_admin_role(current_user)
    try:
        require_admin_role(current_user)
        await require_platform_permissions(current_user, ["users:write"])
        # Check if user exists
        existing_user = await auth_service.get_user(user_id)
        if not existing_user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # Update user
        updated_user = await auth_service.update_user(user_id, user_data)

        from app.core.response_mappers import to_user_response

        return to_user_response(updated_user)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update user: {str(e)}")
