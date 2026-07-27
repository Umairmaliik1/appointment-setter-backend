"""
Core security utilities and dependencies.
PERFORMANCE OPTIMIZATION: Using async Redis for non-blocking operations.
"""

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError
from fastapi import HTTPException, status

from app.core.async_redis import async_redis_client
from app.core.config import AWS_ACCESS_KEY_ID, AWS_REGION, AWS_SECRET_ACCESS_KEY

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class RateLimitInfo:
    """Rate limit information."""

    limit: int
    remaining: int
    reset_time: datetime
    retry_after: Optional[int] = None


@dataclass
class IdempotencyKey:
    """Idempotency key information."""

    key: str
    operation: str
    tenant_id: Optional[str]
    user_id: Optional[str]
    created_at: datetime
    expires_at: datetime
    result: Optional[Dict[str, Any]] = None


class SecurityService:
    """Service class for security operations using async Redis."""

    # Redis outage handling for rate limiting (fail-open with short retry cooldown).
    _rate_limit_redis_unavailable_until: float = 0.0
    _rate_limit_redis_unavailable_logged: bool = False
    _rate_limit_redis_cooldown_seconds: int = 30

    def __init__(self):
        """Initialize security service with async Redis client."""
        # Use async Redis client (no initialization needed, it's a singleton)
        self.redis_client = async_redis_client

        # Initialize AWS Secrets Manager if credentials are available
        if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
            self.secrets_manager = boto3.client(
                "secretsmanager",
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                region_name=AWS_REGION,
            )
        else:
            self.secrets_manager = None

    @staticmethod
    def _permissive_rate_limit(limit: int, window_seconds: int) -> RateLimitInfo:
        """Return permissive rate limit information when backend checks are unavailable."""
        return RateLimitInfo(
            limit=limit, remaining=limit, reset_time=datetime.now(timezone.utc) + timedelta(seconds=window_seconds)
        )

    async def check_rate_limit(
        self, identifier: str, limit: int, window_seconds: int, operation: str = "default"
    ) -> RateLimitInfo:
        """Check rate limit for an identifier."""
        now_epoch = time.time()
        if now_epoch < self.__class__._rate_limit_redis_unavailable_until:
            # During cooldown window, skip Redis to avoid repeated connection failures on every request.
            return self._permissive_rate_limit(limit, window_seconds)

        try:
            key = f"rate_limit:{operation}:{identifier}"
            current_time = int(time.time())
            window_start = current_time - window_seconds

            # Use Redis sliding window with async operations
            client = await self.redis_client.get_client()
            pipe = client.pipeline()

            # Remove expired entries
            pipe.zremrangebyscore(key, 0, window_start)

            # Count current requests
            pipe.zcard(key)

            # Add current request
            pipe.zadd(key, {str(current_time): current_time})

            # Set expiration
            pipe.expire(key, window_seconds)

            results = await pipe.execute()
            current_count = results[1]

            if self.__class__._rate_limit_redis_unavailable_logged:
                logger.info("Redis connection restored for rate limiting")
                self.__class__._rate_limit_redis_unavailable_logged = False
                self.__class__._rate_limit_redis_unavailable_until = 0.0

            # Check if limit exceeded
            if current_count >= limit:
                # Get oldest request time
                oldest_request = await self.redis_client.zrange(key, 0, 0, withscores=True)
                if oldest_request:
                    oldest_time = int(oldest_request[0][1])
                    retry_after = oldest_time + window_seconds - current_time
                else:
                    retry_after = window_seconds

                return RateLimitInfo(
                    limit=limit,
                    remaining=0,
                    reset_time=datetime.fromtimestamp(current_time + retry_after, timezone.utc),
                    retry_after=retry_after,
                )

            return RateLimitInfo(
                limit=limit,
                remaining=limit - current_count - 1,
                reset_time=datetime.fromtimestamp(current_time + window_seconds, timezone.utc),
            )

        except Exception as e:
            self.__class__._rate_limit_redis_unavailable_until = now_epoch + self.__class__._rate_limit_redis_cooldown_seconds
            if not self.__class__._rate_limit_redis_unavailable_logged:
                logger.warning(
                    "Rate limiting fallback enabled: Redis unavailable (%s). "
                    "Failing open for %ss before retrying.",
                    e,
                    self.__class__._rate_limit_redis_cooldown_seconds,
                )
                self.__class__._rate_limit_redis_unavailable_logged = True
            return self._permissive_rate_limit(limit, window_seconds)

    async def enforce_rate_limit(self, identifier: str, limit: int, window_seconds: int, operation: str = "default") -> None:
        """Enforce rate limit, raise exception if exceeded."""
        rate_limit_info = await self.check_rate_limit(identifier, limit, window_seconds, operation)

        if rate_limit_info.retry_after:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Rate limit exceeded",
                    "limit": rate_limit_info.limit,
                    "remaining": rate_limit_info.remaining,
                    "reset_time": rate_limit_info.reset_time.isoformat(),
                    "retry_after": rate_limit_info.retry_after,
                },
                headers={
                    "X-RateLimit-Limit": str(rate_limit_info.limit),
                    "X-RateLimit-Remaining": str(rate_limit_info.remaining),
                    "X-RateLimit-Reset": str(int(rate_limit_info.reset_time.timestamp())),
                    "Retry-After": str(rate_limit_info.retry_after),
                },
            )

    async def create_idempotency_key(
        self, operation: str, tenant_id: Optional[str] = None, user_id: Optional[str] = None, ttl_hours: int = 24
    ) -> str:
        """Create idempotency key for operation."""
        try:
            # Generate unique key
            key_data = f"{operation}:{tenant_id or 'global'}:{user_id or 'anonymous'}:{time.time()}"
            key = hashlib.sha256(key_data.encode()).hexdigest()

            # Store in Redis
            expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
            idempotency_data = IdempotencyKey(
                key=key,
                operation=operation,
                tenant_id=tenant_id,
                user_id=user_id,
                created_at=datetime.now(timezone.utc),
                expires_at=expires_at,
            )

            await self.redis_client.setex(f"idempotency:{key}", ttl_hours * 3600, str(idempotency_data.__dict__))

            return key

        except Exception as e:
            logger.error(f"Error creating idempotency key: {e}", exc_info=True)
            return str(uuid.uuid4())

    async def check_idempotency_key(self, key: str) -> Optional[Dict[str, Any]]:
        """Check if idempotency key exists and return result."""
        try:
            data = await self.redis_client.get(f"idempotency:{key}")
            if data:
                # Parse stored data
                import ast

                idempotency_data = ast.literal_eval(data)
                return idempotency_data.get("result")
            return None

        except Exception as e:
            logger.error(f"Error checking idempotency key: {e}", exc_info=True)
            return None

    async def store_idempotency_result(self, key: str, result: Dict[str, Any]) -> None:
        """Store result for idempotency key."""
        try:
            data = await self.redis_client.get(f"idempotency:{key}")
            if data:
                import ast

                idempotency_data = ast.literal_eval(data)
                idempotency_data["result"] = result

                # Update with result
                ttl = await self.redis_client.ttl(f"idempotency:{key}")
                if ttl > 0:
                    await self.redis_client.setex(f"idempotency:{key}", ttl, str(idempotency_data))

        except Exception as e:
            logger.error(f"Error storing idempotency result: {e}", exc_info=True)

    def get_secret(self, secret_name: str) -> Optional[str]:
        """Get secret from AWS Secrets Manager."""
        if not self.secrets_manager:
            return None

        try:
            response = self.secrets_manager.get_secret_value(SecretId=secret_name)
            return response["SecretString"]

        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                logger.warning(f"Secret {secret_name} not found")
            else:
                logger.error(f"Error retrieving secret {secret_name}: {e}", exc_info=True)
            return None

    def store_secret(self, secret_name: str, secret_value: str) -> bool:
        """Store secret in AWS Secrets Manager."""
        if not self.secrets_manager:
            return False

        try:
            self.secrets_manager.create_secret(Name=secret_name, SecretString=secret_value)
            return True

        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceExistsException":
                # Update existing secret
                try:
                    self.secrets_manager.update_secret(SecretId=secret_name, SecretString=secret_value)
                    return True
                except ClientError as update_error:
                    logger.error(f"Error updating secret {secret_name}: {update_error}", exc_info=True)
                    return False
            else:
                logger.error(f"Error creating secret {secret_name}: {e}", exc_info=True)
                return False

    async def generate_api_key(self, tenant_id: str) -> str:
        """Generate API key for tenant."""
        try:
            # Create API key data
            key_data = f"{tenant_id}:{time.time()}:{secrets.token_hex(16)}"
            api_key = hashlib.sha256(key_data.encode()).hexdigest()

            # Store in Redis with tenant mapping
            await self.redis_client.setex(f"api_key:{api_key}", 365 * 24 * 3600, tenant_id)  # 1 year TTL

            return api_key

        except Exception as e:
            logger.error(f"Error generating API key: {e}", exc_info=True)
            return str(uuid.uuid4())

    async def validate_api_key(self, api_key: str) -> Optional[str]:
        """Validate API key and return tenant ID."""
        try:
            tenant_id = await self.redis_client.get(f"api_key:{api_key}")
            return tenant_id

        except Exception as e:
            logger.error(f"Error validating API key: {e}", exc_info=True)
            return None

    async def revoke_api_key(self, api_key: str) -> bool:
        """Revoke API key."""
        try:
            result = await self.redis_client.delete(f"api_key:{api_key}")
            return result > 0

        except Exception as e:
            logger.error(f"Error revoking API key: {e}", exc_info=True)
            return False

    def generate_webhook_signature(self, payload: str, secret: str) -> str:
        """Generate webhook signature."""
        return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def verify_webhook_signature(self, payload: str, signature: str, secret: str) -> bool:
        """Verify webhook signature."""
        expected_signature = self.generate_webhook_signature(payload, secret)
        return hmac.compare_digest(signature, expected_signature)

    def sanitize_input(self, input_data: Any) -> Any:
        """Sanitize input data."""
        if isinstance(input_data, str):
            # Remove potentially dangerous characters
            dangerous_chars = ["<", ">", '"', "'", "&", ";", "(", ")", "|", "`"]
            for char in dangerous_chars:
                input_data = input_data.replace(char, "")
            return input_data.strip()
        elif isinstance(input_data, dict):
            return {key: self.sanitize_input(value) for key, value in input_data.items()}
        elif isinstance(input_data, list):
            return [self.sanitize_input(item) for item in input_data]
        else:
            return input_data

    def get_security_headers(self) -> Dict[str, str]:
        """Get security headers for responses."""
        return {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Content-Security-Policy": "default-src 'self'",
            "Referrer-Policy": "strict-origin-when-cross-origin",
        }

    async def log_security_event(
        self,
        event_type: str,
        user_id: Optional[str],
        tenant_id: Optional[str],
        details: Dict[str, Any],
        severity: str = "info",
    ):
        """Log security event."""
        try:
            security_log = {
                "event_type": event_type,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "details": details,
                "severity": severity,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "ip_address": details.get("ip_address"),
                "user_agent": details.get("user_agent"),
            }

            # Store in Redis for security monitoring as JSON string
            await self.redis_client.lpush("security_events", json.dumps(security_log))

            # Keep only last 1000 events
            await self.redis_client.ltrim("security_events", 0, 999)

        except Exception as e:
            logger.error(f"Error logging security event: {e}", exc_info=True)

    async def get_security_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent security events."""
        try:
            events = await self.redis_client.lrange("security_events", 0, limit - 1)
            return [json.loads(event) for event in events]

        except Exception as e:
            logger.error(f"Error getting security events: {e}", exc_info=True)
            return []

    async def cleanup_expired_keys(self) -> int:
        """Clean up expired idempotency keys."""
        try:
            pattern = "idempotency:*"
            keys = await self.redis_client.keys(pattern)
            cleaned_count = 0

            for key in keys:
                ttl = await self.redis_client.ttl(key)
                if ttl == -1:  # No expiration set
                    await self.redis_client.delete(key)
                    cleaned_count += 1
                elif ttl == -2:  # Key doesn't exist
                    cleaned_count += 1

            return cleaned_count

        except Exception as e:
            logger.error(f"Error cleaning up expired keys: {e}", exc_info=True)
            return 0
