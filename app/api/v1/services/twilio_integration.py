"""
Twilio Integration Service for managing user's Twilio credentials and phone numbers.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import httpx
from twilio.base.exceptions import TwilioException
from twilio.rest import Client

from app.api.v1.schemas.voice_agent import TwilioCredentialTest, TwilioCredentialTestResponse, TwilioIntegrationConfig
from app.core import config as app_config
from app.core.encryption import encryption_service
from app.services.store import store

# Configure logging
logger = logging.getLogger(__name__)


async def _run_twilio_sync(func: Callable) -> Any:
    """
    Helper function to run synchronous Twilio operations in a thread pool.
    Prevents blocking the event loop.
    """
    return await asyncio.to_thread(func)


def _append_credential_verification_hint(message: str) -> str:
    """Add guidance when Twilio rejects credentials (expired, rotated, or invalid)."""
    if not message:
        return message

    lower = message.lower()
    hint = "Please verify your Twilio credentials again."
    if hint.lower() in lower:
        return message

    auth_indicators = (
        "authenticate",
        "authentication failed",
        "401",
        "invalid account sid",
        "invalid auth token",
        "credential test failed",
    )
    if any(indicator in lower for indicator in auth_indicators):
        return f"{message.rstrip('.')}. {hint}"

    return message


class TwilioIntegrationService:
    """Service for managing Twilio integrations for tenants."""

    def __init__(self):
        """Initialize Twilio integration service."""
        pass

    async def _list_active_role_bindings(self, tenant_id: str) -> List[Dict[str, Any]]:
        """List active telephony role bindings for the tenant."""
        phones = await store.list_phones_by_tenant(tenant_id)
        active_bindings: List[Dict[str, Any]] = []
        for phone in phones:
            usage_role = phone.get("usage_role") or "voice_agent_inbound"
            role_status = phone.get("role_status")
            if not role_status:
                role_status = "active" if phone.get("status", "active") == "active" else "inactive"
            if phone.get("status", "active") == "active" and role_status == "active":
                active_bindings.append({**phone, "usage_role": usage_role, "role_status": role_status})
        return active_bindings

    async def test_credentials(self, credentials: TwilioCredentialTest) -> TwilioCredentialTestResponse:
        """Test Twilio credentials and return account information.

        Supports both production and test account credentials. Test accounts are detected
        and validated using alternative methods since they cannot access production-only endpoints.
        """
        try:
            # Wrap all Twilio operations in thread pool to avoid blocking event loop
            def _test_credentials_sync():
                client = Client(credentials.account_sid, credentials.auth_token)

                # Try to fetch account information (works for production accounts only)
                is_test_account = False
                account_info = None

                try:
                    account = client.api.accounts(credentials.account_sid).fetch()
                    account_info = {
                        "account_sid": account.sid,
                        "friendly_name": account.friendly_name,
                        "status": account.status,
                        "type": account.type,
                        "date_created": account.date_created.isoformat() if account.date_created else None,
                        "phone_number": credentials.phone_number,
                    }
                except TwilioException as e:
                    # Check if this is a test account error (error code 20008)
                    error_str = str(e)
                    if (
                        "20008" in error_str
                        or "Test Account Credentials" in error_str
                        or "not accessible with Test Account" in error_str
                    ):
                        is_test_account = True
                        logger.info(f"Detected Twilio test account credentials for {credentials.account_sid}")
                        # For test accounts, validate by listing incoming phone numbers instead
                        try:
                            incoming_numbers = client.incoming_phone_numbers.list(limit=1)
                            # If we can list numbers, credentials are valid (test account)
                            account_info = {
                                "account_sid": credentials.account_sid,
                                "friendly_name": "Test Account",
                                "status": "active",
                                "type": "test",
                                "date_created": None,
                                "phone_number": credentials.phone_number,
                                "note": "Test account credentials detected - some features may be limited",
                            }
                        except TwilioException as test_error:
                            return TwilioCredentialTestResponse(
                                success=False,
                                message=_append_credential_verification_hint(
                                    f"Invalid Twilio test account credentials. Error: {str(test_error)}"
                                ),
                                phone_number=None,
                                account_info=None,
                                is_test_account=True,
                            )
                        except Exception as test_error:
                            return TwilioCredentialTestResponse(
                                success=False,
                                message=f"Error validating test account credentials: {str(test_error)}",
                                phone_number=None,
                                account_info=None,
                                is_test_account=True,
                            )
                    else:
                        # Not a test account, but other error occurred
                        return TwilioCredentialTestResponse(
                            success=False,
                            message=_append_credential_verification_hint(f"Twilio error: {str(e)}"),
                            phone_number=None,
                            account_info=None,
                            is_test_account=False,
                        )

                # If phone number is provided, verify it exists in the account
                if credentials.phone_number:
                    try:
                        incoming_numbers = client.incoming_phone_numbers.list(limit=50)
                        phone_number_exists = False
                        for number in incoming_numbers:
                            if number.phone_number == credentials.phone_number:
                                phone_number_exists = True
                                break

                        if not phone_number_exists:
                            msg = f"Phone number {credentials.phone_number} not found in your Twilio account"
                            if is_test_account:
                                msg += " (test account)"
                            return TwilioCredentialTestResponse(
                                success=False,
                                message=msg,
                                phone_number=None,
                                account_info=account_info,
                                is_test_account=is_test_account,
                            )
                    except TwilioException as e:
                        if is_test_account:
                            # For test accounts, phone number validation might fail - warn but allow
                            logger.warning(f"Could not verify phone number for test account: {e}")
                        else:
                            return TwilioCredentialTestResponse(
                                success=False,
                                message=f"Error verifying phone number: {str(e)}",
                                phone_number=None,
                                account_info=account_info,
                                is_test_account=is_test_account,
                            )

                # Credentials are valid
                message = "Credentials verified successfully"
                if is_test_account:
                    message += " (Test Account - Some features may be limited)"
                    logger.warning(
                        f"Twilio test account detected for {credentials.account_sid}. Some production features will be unavailable."
                    )
                if credentials.phone_number:
                    message += f" and phone number {credentials.phone_number} found"

                return TwilioCredentialTestResponse(
                    success=True,
                    message=message,
                    phone_number=credentials.phone_number,
                    account_info=account_info,
                    is_test_account=is_test_account,
                )

            # Run Twilio operations in thread pool
            return await _run_twilio_sync(_test_credentials_sync)

        except TwilioException as e:
            return TwilioCredentialTestResponse(
                success=False,
                message=_append_credential_verification_hint(f"Twilio error: {str(e)}"),
                phone_number=None,
                account_info=None,
                is_test_account=False,
            )
        except Exception as e:
            return TwilioCredentialTestResponse(
                success=False,
                message=f"Error testing credentials: {str(e)}",
                phone_number=None,
                account_info=None,
                is_test_account=False,
            )

    async def create_integration(self, tenant_id: str, config: TwilioIntegrationConfig) -> Dict[str, Any]:
        """Create Twilio integration for a tenant."""
        try:
            # Test credentials first (phone number is optional for initial setup)
            test_credentials = TwilioCredentialTest(
                account_sid=config.account_sid, auth_token=config.auth_token, phone_number=config.phone_number  # Optional
            )

            test_result = await self.test_credentials(test_credentials)

            if not test_result.success:
                raise Exception(f"Credential test failed: {test_result.message}")

            # Warn about test account limitations
            if test_result.is_test_account:
                logger.warning(
                    f"Creating integration with Twilio test account for tenant {tenant_id}. "
                    "Note: Test accounts have limitations - some production features may not work. "
                    "Use production credentials for full functionality."
                )

            # Determine webhook URLs (fallback to env if not provided)
            try:
                base = app_config.TWILIO_WEBHOOK_BASE_URL.strip("/") if app_config.TWILIO_WEBHOOK_BASE_URL else ""
            except (AttributeError, TypeError):
                base = ""
            effective_webhook = config.webhook_url or (f"{base}/api/v1/voice-agent/twilio/webhook" if base else None)
            effective_status = config.status_callback_url or (f"{base}/api/v1/voice-agent/twilio/status" if base else None)

            # Store integration in PostgreSQL with encrypted auth_token
            integration_data = {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "account_sid": config.account_sid,
                "auth_token": encryption_service.encrypt(config.auth_token),  # Encrypted for security
                "phone_number": config.phone_number or "",  # Optional
                "webhook_url": effective_webhook or "",
                "status_callback_url": effective_status or "",
                "account_info": test_result.account_info,
                "is_test_account": test_result.is_test_account,  # Store test account flag
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            # Store in PostgreSQL
            result = await store.create_twilio_integration(integration_data)

            # Invalidate cache on create
            if result:
                from app.core.cache import invalidate_twilio_integration_cache

                await invalidate_twilio_integration_cache(tenant_id)

            # Configure webhook URL on Twilio (only if phone number provided)
            if config.phone_number:
                try:
                    await self._configure_webhook(
                        config.account_sid, config.auth_token, config.phone_number, effective_webhook, effective_status
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not configure webhook for {config.phone_number}: {e}. Integration saved but webhook not configured."
                    )

            return result

        except Exception as e:
            error_msg = str(e)
            # Provide more specific error messages
            if "Credential test failed" in error_msg:
                # Check if it's a test account error that wasn't handled properly
                if "20008" in error_msg or "Test Account Credentials" in error_msg:
                    raise Exception(
                        f"Twilio test account detected but validation failed: {error_msg}. "
                        "Please ensure your test account credentials are correct and try again."
                    )
                raise Exception(error_msg)
            elif "Twilio error" in error_msg or "Authenticate" in error_msg:
                raise Exception(
                    f"Twilio authentication failed. Please verify your Account SID and Auth Token are correct. Error: {error_msg}"
                )
            else:
                logger.error(f"Unexpected error creating Twilio integration: {e}", exc_info=True)
                raise Exception(f"Failed to create Twilio integration: {error_msg}")

    async def update_integration(self, tenant_id: str, config: TwilioIntegrationConfig) -> Dict[str, Any]:
        """Update Twilio integration for a tenant."""
        try:
            current_integration = await store.get_twilio_integration(tenant_id)
            if current_integration and current_integration.get("account_sid") != config.account_sid:
                active_bindings = await self._list_active_role_bindings(tenant_id)
                if active_bindings:
                    raise ValueError(
                        "Cannot change shared Twilio Account SID while active phone ownership bindings exist. "
                        "Unbind numbers from voice/cold-caller roles first."
                    )

            # Test new credentials (phone number optional)
            test_credentials = TwilioCredentialTest(
                account_sid=config.account_sid, auth_token=config.auth_token, phone_number=config.phone_number  # Optional
            )

            test_result = await self.test_credentials(test_credentials)

            if not test_result.success:
                raise Exception(f"Credential test failed: {test_result.message}")

            # Warn about test account limitations
            if test_result.is_test_account:
                logger.warning(
                    f"Updating integration with Twilio test account for tenant {tenant_id}. "
                    "Note: Test accounts have limitations - some production features may not work. "
                    "Use production credentials for full functionality."
                )

            # Determine webhook URLs (fallback to env if not provided)
            try:
                base = app_config.TWILIO_WEBHOOK_BASE_URL.strip("/") if app_config.TWILIO_WEBHOOK_BASE_URL else ""
            except (AttributeError, TypeError):
                base = ""
            effective_webhook = config.webhook_url or (f"{base}/api/v1/voice-agent/twilio/webhook" if base else None)
            effective_status = config.status_callback_url or (f"{base}/api/v1/voice-agent/twilio/status" if base else None)

            # Update integration data with encrypted auth_token
            update_data = {
                "account_sid": config.account_sid,
                "auth_token": encryption_service.encrypt(config.auth_token),  # Encrypted for security
                "phone_number": config.phone_number or "",
                "webhook_url": effective_webhook or "",
                "status_callback_url": effective_status or "",
                "account_info": test_result.account_info,
                "is_test_account": test_result.is_test_account,  # Store test account flag
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            # Update in PostgreSQL
            result = await store.update_twilio_integration(tenant_id, update_data)

            # Invalidate cache on update
            if result:
                from app.core.cache import invalidate_twilio_integration_cache

                await invalidate_twilio_integration_cache(tenant_id)

            # Update webhook configuration on Twilio (only if phone number provided)
            if config.phone_number:
                try:
                    await self._configure_webhook(
                        config.account_sid, config.auth_token, config.phone_number, effective_webhook, effective_status
                    )
                except Exception as e:
                    logger.warning(f"Could not update webhook for {config.phone_number}: {e}")

            return result

        except ValueError:
            raise
        except Exception as e:
            raise Exception(f"Failed to update Twilio integration: {str(e)}")

    async def get_integration(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Get Twilio integration for a tenant with decrypted auth_token (with caching)."""
        from app.core.cache import get_cached_twilio_integration, set_cached_twilio_integration

        # Check cache first
        cached_integration = await get_cached_twilio_integration(tenant_id)
        if cached_integration:
            integration = cached_integration
        else:
            # Cache miss: fetch from PostgreSQL
            integration = await store.get_twilio_integration(tenant_id)
            if integration:
                # Store in cache (with encrypted auth_token)
                await set_cached_twilio_integration(tenant_id, integration)

        # Decrypt auth_token before returning (whether from cache or PostgreSQL)
        if integration and "auth_token" in integration:
            # Decrypt auth_token before returning
            try:
                integration["auth_token"] = encryption_service.decrypt(integration["auth_token"])
            except Exception as e:
                logger.error(f"Failed to decrypt auth_token for tenant {tenant_id}: {e}")
                # Return None if decryption fails for security
                return None
        return integration

    async def delete_integration(self, tenant_id: str) -> bool:
        """Delete Twilio integration for a tenant."""
        try:
            active_bindings = await self._list_active_role_bindings(tenant_id)
            if active_bindings:
                raise ValueError(
                    "Cannot delete shared Twilio credentials while active phone ownership bindings exist."
                )

            # Get current integration
            integration = await self.get_integration(tenant_id)

            if not integration:
                return False

            # Remove webhook configuration from Twilio
            if integration.get("phone_number"):
                await self._remove_webhook(integration["account_sid"], integration["auth_token"], integration["phone_number"])

            # Delete from PostgreSQL
            result = await store.delete_twilio_integration(tenant_id)

            # Invalidate cache on delete
            if result is not None:
                from app.core.cache import invalidate_twilio_integration_cache

                await invalidate_twilio_integration_cache(tenant_id)

            return result is not None

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error deleting Twilio integration: {e}", exc_info=True)
            return False

    async def _configure_webhook(
        self, account_sid: str, auth_token: str, phone_number: str, webhook_url: str, status_callback_url: str
    ):
        """Configure webhook URL on Twilio phone number."""
        try:

            def _configure_webhook_sync():
                client = Client(account_sid, auth_token)

                # Get the phone number resource
                incoming_numbers = client.incoming_phone_numbers.list()

                for number in incoming_numbers:
                    if number.phone_number == phone_number:
                        # Update webhook URLs
                        number.update(
                            voice_url=webhook_url,
                            voice_method="POST",
                            status_callback=status_callback_url,
                            status_callback_method="POST",
                        )
                        break

            await _run_twilio_sync(_configure_webhook_sync)

        except Exception as e:
            logger.error(f"Error configuring webhook: {e}", exc_info=True)
            raise

    async def _remove_webhook(self, account_sid: str, auth_token: str, phone_number: str):
        """Remove webhook configuration from Twilio phone number."""
        try:

            def _remove_webhook_sync():
                client = Client(account_sid, auth_token)

                # Get the phone number resource
                incoming_numbers = client.incoming_phone_numbers.list()

                for number in incoming_numbers:
                    if number.phone_number == phone_number:
                        # Remove webhook URLs
                        number.update(voice_url="", voice_method="POST", status_callback="", status_callback_method="POST")
                        break

            await _run_twilio_sync(_remove_webhook_sync)

        except Exception as e:
            logger.error(f"Error removing webhook: {e}", exc_info=True)
            raise

    async def get_available_phone_numbers(self, account_sid: str, auth_token: str) -> List[Dict[str, Any]]:
        """Get available phone numbers from Twilio account."""
        try:

            def _get_available_phone_numbers_sync():
                client = Client(account_sid, auth_token)

                # Get incoming phone numbers
                incoming_numbers = client.incoming_phone_numbers.list(limit=50)

                phone_numbers = []
                for number in incoming_numbers:
                    phone_numbers.append(
                        {
                            "phone_number": number.phone_number,
                            "friendly_name": number.friendly_name,
                            "voice_url": number.voice_url,
                            "voice_method": number.voice_method,
                            "status_callback": number.status_callback,
                            "status_callback_method": number.status_callback_method,
                            "capabilities": {
                                "voice": number.capabilities.get("voice", False),
                                "sms": number.capabilities.get("sms", False),
                                "mms": number.capabilities.get("mms", False),
                            },
                        }
                    )

                return phone_numbers

            return await _run_twilio_sync(_get_available_phone_numbers_sync)

        except TwilioException as e:
            logger.error(f"Twilio auth/permission error while listing numbers: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Error getting phone numbers: {e}", exc_info=True)
            raise

    async def get_owned_numbers(self, account_sid: str, auth_token: str) -> List[Dict[str, Any]]:
        """Return numbers owned in the Twilio account (similar to get_available_phone_numbers)."""
        return await self.get_available_phone_numbers(account_sid, auth_token)

    async def get_unassigned_numbers_for_tenant(
        self, tenant_id: str, account_sid: str, auth_token: str
    ) -> List[Dict[str, Any]]:
        """Compute unassigned numbers = owned numbers minus numbers already stored/assigned in our DB for tenant.

        A phone number is considered assigned if it exists in our phone_numbers collection for the tenant.
        """
        owned = await self.get_owned_numbers(account_sid, auth_token)

        # Fetch numbers already tracked for this tenant
        try:
            existing = await store.list_phones_by_tenant(tenant_id)
        except Exception:
            existing = []

        # A stored number is only "in use" (and therefore NOT available to
        # re-assign) when it is actually bound: it has an agent, or it has an
        # active role binding (e.g. cold-caller outbound). A number whose row
        # still exists but has been unassigned (agent_id="", status/role
        # "inactive") — or one that was just purchased into the pool — must be
        # returned here so it reappears in the "Unassigned Numbers" panel.
        # Without this, unassigning a number makes it vanish from the UI
        # entirely (no longer "assigned", yet still excluded from "unassigned").
        assigned_set = set()
        for p in existing or []:
            n = p.get("phone_number")
            if not n:
                continue
            role_status = p.get("role_status")
            if not role_status:
                role_status = "active" if p.get("status", "active") == "active" else "inactive"
            in_use = bool(p.get("agent_id")) or (
                p.get("status", "active") == "active" and role_status == "active"
            )
            if in_use:
                assigned_set.add(n)

        unassigned = []
        for n in owned:
            if n.get("phone_number") not in assigned_set:
                unassigned.append(n)

        return unassigned

    async def search_available_numbers(
        self,
        account_sid: str,
        auth_token: str,
        country: str = "US",
        number_type: str = "local",
        area_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search available numbers in Twilio by country and type.

        number_type can be 'local' or 'tollfree'.
        """
        try:

            def _search_available_numbers_sync():
                client = Client(account_sid, auth_token)

                kwargs: Dict[str, Any] = {"limit": 30}
                if area_code and number_type == "local":
                    # Twilio local supports area_code
                    kwargs["area_code"] = area_code

                numbers = []
                if number_type == "local":
                    available = client.available_phone_numbers(country).local.list(**kwargs)
                else:
                    available = client.available_phone_numbers(country).toll_free.list(**kwargs)

                for num in available:
                    numbers.append(
                        {
                            "phone_number": getattr(num, "phone_number", None),
                            "friendly_name": getattr(num, "friendly_name", None),
                            "iso_country": getattr(num, "iso_country", country),
                            "capabilities": getattr(num, "capabilities", {}),
                        }
                    )

                return numbers

            return await _run_twilio_sync(_search_available_numbers_sync)
        except TwilioException as e:
            logger.error(f"Twilio auth/permission error while searching numbers: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Error searching available numbers: {e}", exc_info=True)
            raise

    async def purchase_phone_number(
        self,
        account_sid: str,
        auth_token: str,
        tenant_id: str,
        phone_number: str,
        webhook_url: Optional[str] = None,
        status_callback_url: Optional[str] = None,
        is_system_purchase: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Purchase a phone number in Twilio, configure webhooks, and store in tenant's pool (unassigned).

        Args:
            account_sid: Twilio Account SID
            auth_token: Twilio Auth Token
            tenant_id: Tenant ID to associate the number with
            phone_number: Phone number to purchase (E.164 format)
            webhook_url: Optional webhook URL (falls back to TWILIO_WEBHOOK_BASE_URL)
            status_callback_url: Optional status callback URL (falls back to TWILIO_WEBHOOK_BASE_URL)
            is_system_purchase: If True, allows purchase without tenant integration (for system account purchases)
        """
        try:
            # Determine webhook URLs (fallback to env base if not provided)
            base = app_config.TWILIO_WEBHOOK_BASE_URL.strip("/") if app_config.TWILIO_WEBHOOK_BASE_URL else ""
            effective_webhook = webhook_url or (f"{base}/api/v1/voice-agent/twilio/webhook" if base else None)
            effective_status = status_callback_url or (f"{base}/api/v1/voice-agent/twilio/status" if base else None)

            def _purchase_phone_number_sync():
                client = Client(account_sid, auth_token)

                # Create incoming phone number (purchase) - Twilio API requires phone_number parameter
                incoming = client.incoming_phone_numbers.create(phone_number=phone_number)

                # Configure webhooks immediately after purchase (best practice per Twilio docs)
                try:
                    if effective_webhook or effective_status:
                        incoming.update(
                            voice_url=effective_webhook or "",
                            voice_method="POST",
                            status_callback=effective_status or "",
                            status_callback_method="POST",
                        )
                        logger.info(f"Webhooks configured for purchased number {phone_number}")
                except TwilioException as e:
                    # Non-fatal: log but continue to store number
                    logger.warning(
                        f"Failed to set webhooks on purchased number {phone_number}: {e}. Number purchased but webhooks not configured."
                    )
                except Exception as e:
                    logger.warning(f"Unexpected error configuring webhooks: {e}")

                return incoming

            # Run Twilio purchase operation in thread pool
            await _run_twilio_sync(_purchase_phone_number_sync)

            # Handle integration linking
            integration = await store.get_twilio_integration(tenant_id)
            integration_id = None

            if integration:
                integration_id = integration["id"]
            elif is_system_purchase:
                # For system purchases, we can create a temporary system integration record
                # or use a system-level marker. For now, we'll use empty string.
                # TODO: Consider creating a system-level integration entry
                integration_id = "system"
                logger.info("System purchase: No tenant integration found, using system marker")
            else:
                # For tenant purchases, require integration
                raise Exception(
                    "Twilio integration not configured for this tenant. Please configure integration first or use system purchase."
                )

            # Store phone number in tenant pool as inactive and without agent assignment
            phone_dict = {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "phone_number": phone_number,
                "agent_id": "",  # unassigned
                "twilio_integration_id": integration_id or "system",
                "status": "inactive",
                "usage_role": "voice_agent_inbound",
                "role_status": "inactive",
                "conflict_code": None,
                "conflict_message": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            created = await store.create_phone_number(phone_dict)
            logger.info(f"Successfully purchased and stored phone number {phone_number} for tenant {tenant_id}")
            return created

        except TwilioException as e:
            logger.error(f"Twilio API error purchasing phone number {phone_number}: {e}", exc_info=True)
            raise Exception(f"Twilio API error: {str(e)}")
        except Exception as e:
            logger.error(f"Error purchasing phone number: {e}", exc_info=True)
            raise

    async def validate_webhook_url(self, webhook_url: str) -> bool:
        """Validate webhook URL format and accessibility."""
        try:
            # Basic URL validation
            if not webhook_url.startswith(("http://", "https://")):
                return False

            # Test if URL is accessible (basic check)
            async with httpx.AsyncClient() as client:
                response = await client.get(webhook_url, timeout=5.0)
                return response.status_code < 500

        except Exception:
            return False


# Global Twilio integration service instance
twilio_integration_service = TwilioIntegrationService()
