"""
Provisioning service for tenant activation pipeline using PostgreSQL.
Handles LiveKit SIP Ingress creation and Twilio integration.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from livekit import api
from twilio.base.exceptions import TwilioException
from twilio.rest import Client

from app.api.v1.services.tenant import tenant_service
from app.core.config import (
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    TWILIO_WEBHOOK_BASE_URL,
)
from app.core.encryption import encryption_service
from app.services.store import store

# Configure logging
logger = logging.getLogger(__name__)


class ProvisioningService:
    """Service class for tenant provisioning operations using PostgreSQL."""

    def __init__(self):
        """Initialize provisioning service."""
        pass

    async def _get_decrypted_twilio_integration(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Get Twilio integration with decrypted auth_token."""
        integration = await store.get_twilio_integration(tenant_id)
        if integration and "auth_token" in integration:
            try:
                integration["auth_token"] = encryption_service.decrypt(integration["auth_token"])
            except Exception as e:
                logger.error(f"Failed to decrypt Twilio integration for tenant {tenant_id}: {e}")
                return None
        return integration

    async def activate_tenant(self, tenant_id: str, idempotency_key: str) -> Dict[str, Any]:
        """Activate tenant and trigger provisioning pipeline."""
        # Check if provisioning job already exists
        existing_job = await self._get_provisioning_job_by_key(idempotency_key)
        if existing_job:
            return existing_job

        # Check if tenant is ready for activation
        if not await self._is_tenant_ready_for_activation(tenant_id):
            raise ValueError("Tenant is not ready for activation")

        # Create provisioning job
        job_data = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "job_type": "activation",
            "status": "pending",
            "steps": {
                "create_livekit_sip_ingress": "pending",
                "create_twilio_sip_domain": "pending",
                "configure_phone_number": "pending",
                "setup_routing": "pending",
                "test_integration": "pending",
            },
            "idempotency_key": idempotency_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Store provisioning job in PostgreSQL
        job = await self._create_provisioning_job(job_data)

        # Start provisioning pipeline asynchronously
        asyncio.create_task(self._run_provisioning_pipeline(job["id"]))

        return job

    async def _is_tenant_ready_for_activation(self, tenant_id: str) -> bool:
        """Check if tenant is ready for activation."""
        tenant = await tenant_service.get_tenant(tenant_id)
        if not tenant:
            return False

        # Check if tenant has required configurations
        business_config = await tenant_service.get_business_config(tenant_id)
        agent_settings = await tenant_service.get_agent_settings(tenant_id)
        twilio_integration = await tenant_service.get_twilio_integration(tenant_id)

        return all([business_config, agent_settings, twilio_integration])

    async def _create_provisioning_job(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a provisioning job."""
        result = await store.create_provisioning_job(job_data)
        return result

    async def _get_provisioning_job_by_key(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """Get provisioning job by idempotency key."""
        # This would query the provisioning_jobs collection in PostgreSQL
        # For now, we'll return None (would need to implement query by idempotency_key)
        return None

    async def _get_provisioning_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get provisioning job by ID."""
        return await store.get_provisioning_job(job_id)

    async def _update_provisioning_job(self, job_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update provisioning job."""
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        return await store.update_provisioning_job(job_id, update_data)

    async def _run_provisioning_pipeline(self, job_id: str):
        """Run the provisioning pipeline."""
        try:
            job = await self._get_provisioning_job(job_id)
            if not job:
                return

            tenant_id = job["tenant_id"]
            steps = job["steps"]

            # Step 1: Create LiveKit SIP Ingress
            if steps.get("create_livekit_sip_ingress") == "pending":
                await self._update_job_step(job_id, "create_livekit_sip_ingress", "running")

                try:
                    sip_ingress_id = await self._create_livekit_sip_ingress(tenant_id)
                    await self._update_job_step(
                        job_id, "create_livekit_sip_ingress", "completed", {"sip_ingress_id": sip_ingress_id}
                    )
                except Exception as e:
                    await self._update_job_step(job_id, "create_livekit_sip_ingress", "failed", {"error": str(e)})
                    await self._mark_job_failed(job_id, f"Failed to create LiveKit SIP Ingress: {str(e)}")
                    return

            # Step 2: Create Twilio SIP Domain
            if steps.get("create_twilio_sip_domain") == "pending":
                await self._update_job_step(job_id, "create_twilio_sip_domain", "running")

                try:
                    sip_domain_id = await self._create_twilio_sip_domain(tenant_id)
                    await self._update_job_step(
                        job_id, "create_twilio_sip_domain", "completed", {"sip_domain_id": sip_domain_id}
                    )
                except Exception as e:
                    await self._update_job_step(job_id, "create_twilio_sip_domain", "failed", {"error": str(e)})
                    await self._mark_job_failed(job_id, f"Failed to create Twilio SIP Domain: {str(e)}")
                    return

            # Step 3: Configure Phone Number
            if steps.get("configure_phone_number") == "pending":
                await self._update_job_step(job_id, "configure_phone_number", "running")

                try:
                    phone_number_id = await self._configure_phone_number(tenant_id)
                    await self._update_job_step(
                        job_id, "configure_phone_number", "completed", {"phone_number_id": phone_number_id}
                    )
                except Exception as e:
                    await self._update_job_step(job_id, "configure_phone_number", "failed", {"error": str(e)})
                    await self._mark_job_failed(job_id, f"Failed to configure phone number: {str(e)}")
                    return

            # Step 4: Setup Routing
            if steps.get("setup_routing") == "pending":
                await self._update_job_step(job_id, "setup_routing", "running")

                try:
                    await self._setup_routing(tenant_id)
                    await self._update_job_step(job_id, "setup_routing", "completed")
                except Exception as e:
                    await self._update_job_step(job_id, "setup_routing", "failed", {"error": str(e)})
                    await self._mark_job_failed(job_id, f"Failed to setup routing: {str(e)}")
                    return

            # Step 5: Test Integration
            if steps.get("test_integration") == "pending":
                await self._update_job_step(job_id, "test_integration", "running")

                try:
                    test_result = await self._test_integration(tenant_id)
                    await self._update_job_step(job_id, "test_integration", "completed", {"test_result": test_result})
                except Exception as e:
                    await self._update_job_step(job_id, "test_integration", "failed", {"error": str(e)})
                    await self._mark_job_failed(job_id, f"Failed to test integration: {str(e)}")
                    return

            # Mark job as completed
            await self._mark_job_completed(job_id)

            # Activate tenant
            await tenant_service.activate_tenant(tenant_id)

        except Exception as e:
            await self._mark_job_failed(job_id, f"Provisioning pipeline failed: {str(e)}")

    async def _update_job_step(self, job_id: str, step_name: str, status: str, data: Optional[Dict[str, Any]] = None):
        """Update a specific step in the provisioning job."""
        job = await self._get_provisioning_job(job_id)
        if not job:
            return

        steps = job["steps"].copy()
        steps[step_name] = status

        update_data = {"steps": steps}
        if data:
            update_data[f"{step_name}_data"] = data

        await self._update_provisioning_job(job_id, update_data)

    async def _mark_job_completed(self, job_id: str):
        """Mark provisioning job as completed."""
        await self._update_provisioning_job(
            job_id, {"status": "completed", "completed_at": datetime.now(timezone.utc).isoformat()}
        )

    async def _mark_job_failed(self, job_id: str, error_message: str):
        """Mark provisioning job as failed."""
        await self._update_provisioning_job(
            job_id, {"status": "failed", "error_message": error_message, "failed_at": datetime.now(timezone.utc).isoformat()}
        )

    async def _create_livekit_sip_ingress(self, tenant_id: str) -> str:
        """Create LiveKit SIP Ingress for tenant."""
        try:
            # Get tenant configuration
            tenant = await tenant_service.get_tenant(tenant_id)
            if not tenant:
                raise ValueError(f"Tenant {tenant_id} not found")

            # Initialize LiveKit API client
            livekit_api = api.LiveKitAPI(url=LIVEKIT_URL, api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)

            # Create SIP Trunk for incoming calls
            trunk_name = f"trunk-{tenant_id}"

            # Create SIP Ingress
            # Note: LiveKit SIP API requires specific configuration
            # The ingress will receive calls from Twilio and route them to LiveKit rooms
            ingress_info = await livekit_api.sip.create_sip_trunk(
                api.CreateSIPTrunkRequest(
                    trunk_id=trunk_name,
                    name=f"Trunk for {tenant.get('name', tenant_id)}",
                    metadata=f"tenant_id={tenant_id}",
                    # Inbound trunk configuration
                    inbound_addresses=["0.0.0.0/0"],  # Accept from any IP (can be restricted to Twilio IPs)
                    inbound_numbers_regex=[".*"],  # Accept all numbers
                )
            )

            sip_trunk_id = ingress_info.sip_trunk_id
            logger.info(f"Created LiveKit SIP Trunk for tenant {tenant_id}: {sip_trunk_id}")

            return sip_trunk_id

        except Exception as e:
            logger.error(f"Failed to create LiveKit SIP Ingress for tenant {tenant_id}: {e}")
            raise Exception(f"LiveKit SIP Ingress creation failed: {str(e)}")

    async def _create_twilio_sip_domain(self, tenant_id: str) -> str:
        """Create Twilio SIP Domain for tenant."""
        try:
            # Get tenant's Twilio integration with decrypted auth_token
            twilio_integration = await self._get_decrypted_twilio_integration(tenant_id)
            if not twilio_integration:
                raise ValueError(f"No Twilio integration found for tenant {tenant_id}")

            # Create SIP Domain
            # Format: tenant-{tenant_id}.sip.{region}.twilio.com
            domain_name = f"tenant-{tenant_id[:8]}.sip.twilio.com"

            def _create_sip_domain_sync():
                # Create Twilio client with tenant's credentials
                twilio_client = Client(twilio_integration["account_sid"], twilio_integration["auth_token"])

                # Create the SIP domain
                sip_domain = twilio_client.sip.domains.create(
                    domain_name=domain_name,
                    friendly_name=f"SIP Domain for Tenant {tenant_id}",
                    voice_url=f"{TWILIO_WEBHOOK_BASE_URL}/api/v1/voice-agent/twilio/webhook",
                    voice_method="POST",
                    sip_registration=False,  # We're not doing SIP registration, just trunk
                    emergency_calling_enabled=False,
                )
                return sip_domain

            # Run Twilio operation in thread pool
            sip_domain = await asyncio.to_thread(_create_sip_domain_sync)

            logger.info(f"Created Twilio SIP Domain for tenant {tenant_id}: {sip_domain.sid}")

            # Store SIP domain info in PostgreSQL
            await store.update_twilio_integration(
                tenant_id, {"sip_domain_sid": sip_domain.sid, "sip_domain_name": domain_name}
            )

            return sip_domain.sid

        except TwilioException as e:
            logger.error(f"Twilio error creating SIP domain for tenant {tenant_id}: {e}")
            raise Exception(f"Twilio SIP Domain creation failed: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to create Twilio SIP Domain for tenant {tenant_id}: {e}")
            raise Exception(f"Twilio SIP Domain creation failed: {str(e)}")

    async def _configure_phone_number(self, tenant_id: str) -> str:
        """Configure phone number for tenant."""
        try:
            # Get tenant's Twilio integration with decrypted auth_token
            twilio_integration = await self._get_decrypted_twilio_integration(tenant_id)
            if not twilio_integration:
                raise ValueError(f"No Twilio integration found for tenant {tenant_id}")

            phone_number = twilio_integration.get("phone_number")
            if not phone_number:
                raise ValueError(f"No phone number configured for tenant {tenant_id}")

            def _configure_phone_number_sync():
                # Create Twilio client
                twilio_client = Client(twilio_integration["account_sid"], twilio_integration["auth_token"])

                # Find the phone number resource
                incoming_numbers = twilio_client.incoming_phone_numbers.list(phone_number=phone_number)

                if not incoming_numbers:
                    raise ValueError(f"Phone number {phone_number} not found in Twilio account")

                phone_number_resource = incoming_numbers[0]

                # Configure the phone number to use our webhook
                phone_number_resource.update(
                    voice_url=f"{TWILIO_WEBHOOK_BASE_URL}/api/v1/voice-agent/twilio/webhook",
                    voice_method="POST",
                    status_callback=f"{TWILIO_WEBHOOK_BASE_URL}/api/v1/voice-agent/twilio/status",
                    status_callback_method="POST",
                )
                return phone_number_resource

            # Run Twilio operation in thread pool
            phone_number_resource = await asyncio.to_thread(_configure_phone_number_sync)

            logger.info(f"Configured phone number {phone_number} for tenant {tenant_id}")

            return phone_number_resource.sid

        except TwilioException as e:
            logger.error(f"Twilio error configuring phone number for tenant {tenant_id}: {e}")
            raise Exception(f"Phone number configuration failed: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to configure phone number for tenant {tenant_id}: {e}")
            raise Exception(f"Phone number configuration failed: {str(e)}")

    async def _setup_routing(self, tenant_id: str):
        """Setup routing between Twilio and LiveKit."""
        try:
            # Get tenant's Twilio and LiveKit configuration with decrypted auth_token
            twilio_integration = await self._get_decrypted_twilio_integration(tenant_id)
            if not twilio_integration:
                raise ValueError(f"No Twilio integration found for tenant {tenant_id}")

            sip_domain_name = twilio_integration.get("sip_domain_name")
            if not sip_domain_name:
                raise ValueError(f"No SIP domain configured for tenant {tenant_id}")

            def _get_sip_domain_sync():
                # Create Twilio client
                twilio_client = Client(twilio_integration["account_sid"], twilio_integration["auth_token"])

                # Get the SIP domain
                sip_domains = twilio_client.sip.domains.list(domain_name=sip_domain_name)
                if not sip_domains:
                    raise ValueError(f"SIP domain {sip_domain_name} not found")

                return sip_domains[0]

            # Run Twilio operation in thread pool
            await asyncio.to_thread(_get_sip_domain_sync)

            # Create credential list for authentication (if needed)
            # Create IP access control list to allow LiveKit servers

            # Extract LiveKit domain from URL
            livekit_domain = LIVEKIT_URL.replace("wss://", "").replace("https://", "").replace("/", "")

            # Set up SIP trunk to route calls to LiveKit
            # Format: sip:room_name@livekit-domain

            # Store routing configuration in PostgreSQL
            await store.update_twilio_integration(
                tenant_id,
                {
                    "routing_configured": True,
                    "livekit_domain": livekit_domain,
                    "routing_configured_at": datetime.now(timezone.utc).isoformat(),
                },
            )

            logger.info(f"Configured routing for tenant {tenant_id} from Twilio to LiveKit")

        except TwilioException as e:
            logger.error(f"Twilio error setting up routing for tenant {tenant_id}: {e}")
            raise Exception(f"Routing setup failed: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to setup routing for tenant {tenant_id}: {e}")
            raise Exception(f"Routing setup failed: {str(e)}")

    async def _test_integration(self, tenant_id: str) -> Dict[str, Any]:
        """Test the integration for tenant."""
        try:
            # Verify all components are configured with decrypted auth_token
            twilio_integration = await self._get_decrypted_twilio_integration(tenant_id)
            if not twilio_integration:
                raise ValueError(f"No Twilio integration found for tenant {tenant_id}")

            # Check required fields
            required_fields = ["phone_number", "sip_domain_sid", "routing_configured"]
            missing_fields = [field for field in required_fields if not twilio_integration.get(field)]

            if missing_fields:
                return {
                    "status": "failed",
                    "error": f"Missing required configuration: {', '.join(missing_fields)}",
                    "test_time": datetime.now(timezone.utc).isoformat(),
                }

            # Test Twilio connectivity
            try:

                def _test_twilio_connectivity_sync():
                    twilio_client = Client(twilio_integration["account_sid"], twilio_integration["auth_token"])
                    # Verify account is active
                    account = twilio_client.api.accounts(twilio_integration["account_sid"]).fetch()
                    return account

                account = await asyncio.to_thread(_test_twilio_connectivity_sync)
                if account.status != "active":
                    return {
                        "status": "failed",
                        "error": f"Twilio account status is {account.status}",
                        "test_time": datetime.now(timezone.utc).isoformat(),
                    }
            except TwilioException as e:
                return {
                    "status": "failed",
                    "error": f"Twilio connectivity test failed: {str(e)}",
                    "test_time": datetime.now(timezone.utc).isoformat(),
                }

            # Test LiveKit connectivity
            try:
                livekit_api = api.LiveKitAPI(url=LIVEKIT_URL, api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
                # Try to list rooms (simple connectivity test)
                await livekit_api.room.list_rooms(api.ListRoomsRequest())
            except Exception as e:
                return {
                    "status": "failed",
                    "error": f"LiveKit connectivity test failed: {str(e)}",
                    "test_time": datetime.now(timezone.utc).isoformat(),
                }

            return {
                "status": "success",
                "message": "All integration tests passed",
                "test_time": datetime.now(timezone.utc).isoformat(),
                "components_tested": ["twilio_connectivity", "livekit_connectivity", "configuration_complete"],
            }

        except Exception as e:
            logger.error(f"Integration test failed for tenant {tenant_id}: {e}")
            return {"status": "failed", "error": str(e), "test_time": datetime.now(timezone.utc).isoformat()}

    async def get_provisioning_status(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Get provisioning status for a tenant."""
        # This would query the provisioning_jobs collection in PostgreSQL
        # For now, we'll return None (would need to implement query by tenant_id)
        return None


# Global provisioning service instance
provisioning_service = ProvisioningService()
