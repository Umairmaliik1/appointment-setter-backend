"""
SIP Configuration Service for automating LiveKit and Twilio SIP setup.
Handles SIP trunk, dispatch rules, and phone number webhook configuration.

ARCHITECTURE:
- Each phone number gets its own SIP inbound trunk
- Each phone number gets its own dispatch rule (auto-created)
- ALL dispatch rules use single global agentName: "voice-agent"
- One worker handles ALL phone numbers (multi-tenant design)
- Dispatch rule uses Individual mode with roomPrefix="call-"
- Tenant routing is handled via SIP headers (X-LK-TenantId, X-LK-CallId)
- Worker extracts phone number from SIP headers (X-LK-CalledNumber)
"""

import asyncio
import logging
import re
from typing import Any, Callable, Dict, Optional

from livekit import api
from twilio.base.exceptions import TwilioException
from twilio.rest import Client

from app.core.config import (
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_SIP_DOMAIN,
    LIVEKIT_URL,
)
from app.core.encryption import encryption_service
from app.services.store import store
from app.utils.phone_number import normalize_phone_number, normalize_phone_number_safe

# Configure logging
logger = logging.getLogger(__name__)

# Expected dispatch rule configuration
EXPECTED_ROOM_PREFIX = "call-"
# Single global agent name - one worker handles all phone numbers
GLOBAL_AGENT_NAME = "voice-agent"


async def _run_twilio_sync(func: Callable) -> Any:
    """Helper function to run synchronous Twilio operations in a thread pool."""
    return await asyncio.to_thread(func)


class SIPConfigurationService:
    """Service for managing SIP configuration between LiveKit and Twilio."""

    def __init__(self):
        """Initialize SIP configuration service."""
        self._livekit_api = None

    def _get_livekit_api(self) -> api.LiveKitAPI:
        """
        Get LiveKit API client instance (singleton pattern).
        
        Reuses the same instance to avoid creating multiple aiohttp sessions.
        """
        if not LIVEKIT_URL or not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
            raise ValueError(
                "LiveKit configuration is missing. Please set LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET."
            )
        
        if self._livekit_api is None:
            self._livekit_api = api.LiveKitAPI(
                url=LIVEKIT_URL, 
                api_key=LIVEKIT_API_KEY, 
                api_secret=LIVEKIT_API_SECRET
            )
        
        return self._livekit_api

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

    async def _find_trunk_id_by_number(self, livekit_api: api.LiveKitAPI, phone_number: str) -> Optional[str]:
        """
        Look up an existing SIP trunk that has this phone number in its `numbers` list.

        Returns the `sip_trunk_id` (e.g., ST_xxx) if found.
        """
        normalized_phone = normalize_phone_number_safe(phone_number) or phone_number

        if not hasattr(livekit_api.sip, "list_sip_inbound_trunk"):
            logger.warning("LiveKit SDK missing list_sip_inbound_trunk; cannot verify trunk ID")
            return None

        try:
            response = await livekit_api.sip.list_sip_inbound_trunk(api.ListSIPInboundTrunkRequest())
            trunks = response.items if hasattr(response, "items") else response or []

            for trunk in trunks:
                trunk_numbers = getattr(trunk, "numbers", []) or []
                if normalized_phone in trunk_numbers or phone_number in trunk_numbers:
                    sip_trunk_id = (
                        getattr(trunk, "sip_trunk_id", None)
                        or getattr(trunk, "inbound_trunk_id", None)
                        or getattr(trunk, "trunk_id", None)
                        or getattr(trunk, "id", None)
                    )
                    if sip_trunk_id:
                        logger.info(
                            f"[TRUNK LOOKUP] Found existing trunk {sip_trunk_id} containing {normalized_phone}"
                        )
                        return sip_trunk_id
        except Exception as e:
            logger.warning(f"[TRUNK LOOKUP] Failed to list trunks: {e}")

        return None

    def _extract_trunk_id_from_conflict_error(self, error_message: str) -> Optional[str]:
        """
        Extract the conflicting trunk ID from a LiveKit conflict error message.
        
        Error format example:
        "Conflicting inbound SIP Trunks: "<new>" and "ST_s5G6WaGoPVL4", using the same number(s)..."
        
        Returns the trunk ID (e.g., "ST_s5G6WaGoPVL4") if found, None otherwise.
        """
        # Pattern to match trunk IDs in the error message
        # Look for patterns like "ST_xxx" or similar trunk ID formats
        patterns = [
            r'"([ST]_[A-Za-z0-9]+)"',  # Matches "ST_xxxxx" format (quoted)
            r'and\s+"([ST]_[A-Za-z0-9]+)"',  # Matches trunk ID after "and" (quoted)
            r'Trunk[^"]*"([ST]_[A-Za-z0-9]+)"',  # Matches trunk ID in various formats (quoted)
            r'([ST]_[A-Za-z0-9]+)',  # Matches unquoted trunk ID as fallback
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, error_message)
            for match in matches:
                # Skip "<new>" placeholder and ensure it's a valid trunk ID
                if match and match.lower() not in ["<new>", "new"] and match.startswith(("ST_", "IT_", "TR_")):
                    logger.info(f"[TRUNK] Extracted trunk ID from conflict error: {match}")
                    return match
        
        # Fallback: Try to find any ST_ pattern in the error
        fallback_match = re.search(r'(ST_[A-Za-z0-9]+)', error_message)
        if fallback_match:
            trunk_id = fallback_match.group(1)
            logger.info(f"[TRUNK] Extracted trunk ID using fallback pattern: {trunk_id}")
            return trunk_id
        
        logger.warning(f"[TRUNK] Could not extract trunk ID from conflict error: {error_message}")
        return None

    async def _verify_trunk_exists(self, livekit_api: api.LiveKitAPI, trunk_id: str) -> bool:
        """
        Verify that a trunk ID exists and is valid in LiveKit.
        
        Returns True if trunk exists, False otherwise.
        """
        if not trunk_id or not trunk_id.startswith(("ST_", "IT_", "TR_")):
            logger.warning(f"[TRUNK] Invalid trunk ID format: {trunk_id}")
            return False
        
        try:
            if not hasattr(livekit_api.sip, "list_sip_inbound_trunk"):
                logger.warning("[TRUNK] Cannot verify trunk - SDK missing list method")
                return False
            
            response = await livekit_api.sip.list_sip_inbound_trunk(api.ListSIPInboundTrunkRequest())
            trunks = response.items if hasattr(response, "items") else response or []
            
            for trunk in trunks:
                trunk_id_attr = (
                    getattr(trunk, "sip_trunk_id", None)
                    or getattr(trunk, "inbound_trunk_id", None)
                    or getattr(trunk, "trunk_id", None)
                    or getattr(trunk, "id", None)
                )
                if trunk_id_attr == trunk_id:
                    logger.info(f"[TRUNK] Verified trunk exists: {trunk_id}")
                    return True
            
            logger.warning(f"[TRUNK] Trunk {trunk_id} not found in LiveKit")
            return False
            
        except Exception as e:
            logger.error(f"[TRUNK] Failed to verify trunk existence: {e}")
            return False

    async def _delete_sip_trunk(self, livekit_api: api.LiveKitAPI, trunk_id: str) -> bool:
        """
        Delete a SIP inbound trunk by ID.
        
        NOTE: LiveKit SDK may not support trunk deletion via API.
        If deletion fails, the trunk must be manually deleted in LiveKit Dashboard.
        
        Returns True if successful, False otherwise.
        """
        if not trunk_id or not trunk_id.startswith(("ST_", "IT_", "TR_")):
            logger.warning(f"[TRUNK] Invalid trunk ID format for deletion: {trunk_id}")
            return False
        
        try:
            sip_service = livekit_api.sip
            
            # Check for delete method - try multiple possible names
            delete_method = None
            for method_name in ["delete_sip_inbound_trunk", "delete_inbound_trunk", "delete_trunk"]:
                if hasattr(sip_service, method_name):
                    delete_method = getattr(sip_service, method_name)
                    logger.debug(f"[TRUNK] Found delete method: {method_name}")
                    break
            
            if not delete_method:
                logger.error(f"[TRUNK] CRITICAL: No delete method found in LiveKit SDK. Trunk {trunk_id} must be manually deleted in LiveKit Dashboard.")
                logger.error(f"[TRUNK] Available SIP methods: {[m for m in dir(sip_service) if not m.startswith('_')]}")
                return False
            
            # Try different request formats
            request_classes = []
            if hasattr(api, "DeleteSIPInboundTrunkRequest"):
                request_classes.append(("DeleteSIPInboundTrunkRequest", ["sip_trunk_id", "inbound_trunk_id", "trunk_id"]))
            elif hasattr(api, "DeleteInboundTrunkRequest"):
                request_classes.append(("DeleteInboundTrunkRequest", ["trunk_id", "sip_trunk_id"]))
            
            for req_class_name, param_names in request_classes:
                req_class = getattr(api, req_class_name)
                for param_name in param_names:
                    try:
                        request = req_class(**{param_name: trunk_id})
                        await delete_method(request)
                        logger.info(f"[TRUNK] ✓ Deleted trunk {trunk_id} using {req_class_name}({param_name}={trunk_id})")
                        return True
                    except (TypeError, AttributeError, ValueError) as e:
                        logger.debug(f"[TRUNK] Delete attempt failed with {req_class_name}({param_name}): {e}")
                        continue
                    except Exception as e:
                        # If it's a real API error (not a parameter error), log and fail
                        error_str = str(e).lower()
                        if "not found" in error_str or "404" in error_str:
                            logger.warning(f"[TRUNK] Trunk {trunk_id} not found (may already be deleted): {e}")
                            return True  # Consider "not found" as success
                        logger.error(f"[TRUNK] Failed to delete trunk {trunk_id}: {e}")
                        return False
            
            logger.error(f"[TRUNK] All delete attempts failed for trunk {trunk_id}")
            return False
                        
        except Exception as e:
            logger.error(f"[TRUNK] Exception during trunk deletion: {e}")
            return False

    async def _get_or_create_sip_trunk(self, phone_number: str) -> str:
        """
        Get existing SIP trunk or create new one for phone number.

        Per documentation: https://docs.livekit.io/sip/trunk-inbound/
        We use phone-number-specific trunks (numbers=[phone_number]) to avoid conflicts.
        
        IMPORTANT: trunk metadata does NOT contain tenant_id because:
        - Trunks belong to phone numbers, not tenants
        - Multiple tenants can share the same phone number
        - Tenant routing is done via SIP headers

        Returns trunk_id.
        """
        try:
            livekit_api = self._get_livekit_api()

            # Normalize phone number for consistent trunk naming and matching
            normalized_phone = normalize_phone_number(phone_number)
            logger.info(f"[TRUNK] Normalized phone number: {phone_number} -> {normalized_phone}")

            # Check if trunk already exists for this phone number - reuse it if found
            existing_trunk_id = await self._find_trunk_id_by_number(livekit_api, phone_number)
            if existing_trunk_id:
                logger.info(f"[TRUNK] Found existing trunk {existing_trunk_id} for {normalized_phone}, reusing it")
                return existing_trunk_id

            # Create new SIP trunk (always create fresh, never reuse)
            sip_service = livekit_api.sip
            
            if not hasattr(sip_service, "create_sip_inbound_trunk"):
                raise AttributeError("LiveKit SIP service does not have 'create_sip_inbound_trunk' method.")

            try:
                # Create SIP inbound trunk with custom header mapping
                # CRITICAL: Configure headers_to_attributes to map custom SIP headers
                # to LiveKit participant attributes so the worker can read them
                inbound_trunk_info = await sip_service.create_sip_inbound_trunk(
                    api.CreateSIPInboundTrunkRequest(
                        trunk=api.SIPInboundTrunkInfo(
                            name=f"Trunk for {normalized_phone}",
                            allowed_addresses=["0.0.0.0/0"],
                            # CRITICAL: Include ALL SIP headers so custom X-* headers are forwarded
                            # Without this, LiveKit will not receive X-LK-CallId, X-LK-TenantId, etc.
                            include_headers="SIP_ALL_HEADERS",
                            # Map custom SIP headers to participant attributes
                            # Twilio sends X-LK-* headers → LiveKit exposes as lk_* attributes
                            headers_to_attributes={
                                "X-LK-CalledNumber": "lk_callednumber",
                                "X-LK-TenantId": "lk_tenantid",
                                "X-LK-CallId": "lk_callid",
                            },
                        )
                    )
                )

                # Extract trunk ID from response
                sip_trunk_id = None
                
                if hasattr(inbound_trunk_info, "sip_trunk_id"):
                    sip_trunk_id = inbound_trunk_info.sip_trunk_id
                elif hasattr(inbound_trunk_info, "inbound_trunk_id"):
                    sip_trunk_id = inbound_trunk_info.inbound_trunk_id
                elif hasattr(inbound_trunk_info, "trunk_id"):
                    sip_trunk_id = inbound_trunk_info.trunk_id
                elif hasattr(inbound_trunk_info, "id"):
                    sip_trunk_id = inbound_trunk_info.id
                elif isinstance(inbound_trunk_info, str):
                    sip_trunk_id = inbound_trunk_info
                
                if not sip_trunk_id:
                    logger.error("Failed to extract trunk ID from LiveKit response")
                    raise Exception("Could not extract trunk ID from LiveKit API response")

                logger.info(f"[TRUNK] ✓ Created SIP inbound trunk: {sip_trunk_id} (for {normalized_phone})")
                return sip_trunk_id
                
            except Exception as create_error:
                error_str = str(create_error)
                error_str_lower = error_str.lower()

                # Handle "conflicting" trunk errors - reuse the conflicting trunk if it exists
                if "conflicting" in error_str_lower or "conflict" in error_str_lower:
                    conflicting_trunk_id = self._extract_trunk_id_from_conflict_error(error_str)
                    if conflicting_trunk_id:
                        # Verify the conflicting trunk exists and reuse it
                        trunk_exists = await self._verify_trunk_exists(livekit_api, conflicting_trunk_id)
                        if trunk_exists:
                            logger.info(f"[TRUNK] Conflict detected, reusing existing trunk: {conflicting_trunk_id}")
                            return conflicting_trunk_id
                        else:
                            # Trunk ID extracted but doesn't exist - try to find trunk by phone number as fallback
                            logger.warning(f"[TRUNK] Conflicting trunk {conflicting_trunk_id} not found, trying to find by phone number...")
                            existing_trunk_id = await self._find_trunk_id_by_number(livekit_api, phone_number)
                            if existing_trunk_id:
                                logger.info(f"[TRUNK] Found existing trunk by phone number, reusing: {existing_trunk_id}")
                                return existing_trunk_id
                            else:
                                # If we can't find it, still return the extracted ID - it might work
                                logger.warning(f"[TRUNK] Could not verify trunk {conflicting_trunk_id} exists, but will attempt to use it")
                                return conflicting_trunk_id
                    else:
                        # Could not extract trunk ID - try to find by phone number
                        logger.warning("[TRUNK] Could not extract trunk ID from conflict, trying to find by phone number...")
                        existing_trunk_id = await self._find_trunk_id_by_number(livekit_api, phone_number)
                        if existing_trunk_id:
                            logger.info(f"[TRUNK] Found existing trunk by phone number, reusing: {existing_trunk_id}")
                            return existing_trunk_id
                        else:
                            raise Exception(f"Trunk conflict detected but could not extract trunk ID or find existing trunk. Error: {error_str}")

                # Handle "already exists" or "duplicate" errors - reuse existing trunk
                if "already exists" in error_str_lower or "duplicate" in error_str_lower or "409" in error_str:
                    existing_trunk_id = await self._find_trunk_id_by_number(livekit_api, phone_number)
                    if existing_trunk_id:
                        logger.info(f"[TRUNK] Trunk already exists, reusing: {existing_trunk_id}")
                        return existing_trunk_id
                    else:
                        raise Exception(f"Trunk creation failed with 'already exists' but could not find existing trunk: {error_str}")

                logger.error(f"Failed to create SIP inbound trunk: {create_error}")
                raise

        except Exception as e:
            # All error handling is done in the inner try/except block
            # If we reach here, it's a fatal error that couldn't be recovered
            raise Exception(f"Failed to setup SIP trunk: {str(e)}")

    async def _attach_trunk_to_dispatch_rule(
        self, livekit_api: api.LiveKitAPI, trunk_id: str, phone_number: str
    ) -> Optional[str]:
        """
        Ensure a SINGLE global dispatch rule exists and add trunk_id to it.
        
        Architecture: ONE dispatch rule handles ALL phone numbers.
        Each phone number gets its own unique trunk, but all trunks are added to the same rule.
        
        Returns the dispatch_rule_id if successful, None otherwise.
        """
        try:
            # Use single global agent name for all phone numbers
            agent_name = GLOBAL_AGENT_NAME
            # Single global rule name for all phone numbers
            rule_name = "dispatch-rule-global"
            
            logger.info(f"[DISPATCH] Ensuring global dispatch rule with trunk={trunk_id} for phone={phone_number}, agent={agent_name}")
            
            # 1️⃣ Fetch all dispatch rules
            if not hasattr(livekit_api.sip, "list_sip_dispatch_rule"):
                logger.error("[DISPATCH] LiveKit SDK missing list_sip_dispatch_rule method")
                return None
            
            response = await livekit_api.sip.list_sip_dispatch_rule(api.ListSIPDispatchRuleRequest())
            rules = response.items if hasattr(response, "items") else response or []
            
            logger.info(f"[DISPATCH] Found {len(rules)} existing dispatch rules")
            
            # 2️⃣ First, check if ANY existing rule already has this trunk
            # This prevents conflicts when creating new rules
            target_rule = None
            for rule in rules:
                current_trunk_ids = list(getattr(rule, "trunk_ids", []) or [])
                if trunk_id in current_trunk_ids:
                    rule_id = (
                        getattr(rule, "sip_dispatch_rule_id", None)
                        or getattr(rule, "dispatch_rule_id", None)
                        or getattr(rule, "id", None)
                    )
                    rule_name_attr = getattr(rule, "name", None) or "unnamed"
                    logger.info(f"[DISPATCH] ✓ Found existing rule {rule_id} (name: {rule_name_attr}) that already has trunk {trunk_id}")
                    logger.info("[DISPATCH] Reusing existing rule to avoid conflict")
                    return rule_id
            
            # 3️⃣ No rule has this trunk - find or create global rule by name
            for rule in rules:
                rule_name_attr = getattr(rule, "name", None)
                if rule_name_attr == rule_name:
                    target_rule = rule
                    break
            
            # 4️⃣ Create global rule if not found
            if target_rule is None:
                logger.info("[DISPATCH] Creating global dispatch rule (will handle all phone numbers)...")
                
                if not hasattr(livekit_api.sip, "create_sip_dispatch_rule"):
                    logger.error("[DISPATCH] LiveKit SDK missing create_sip_dispatch_rule method")
                    return None
                
                # Build dispatch rule request with agent configuration
                # CRITICAL: Build room config with agent - this is REQUIRED for dispatch to work
                room_config_obj = None
                if hasattr(api, "RoomAgentDispatch") and hasattr(api, "RoomConfiguration"):
                    try:
                        agents_list = [api.RoomAgentDispatch(agent_name=agent_name)]
                        room_config_obj = api.RoomConfiguration(agents=agents_list)
                        logger.debug(f"[DISPATCH] Successfully created RoomConfiguration with agent '{agent_name}'")
                    except Exception as rc_error:
                        logger.error(f"[DISPATCH] CRITICAL: Failed to create RoomConfiguration with agent: {rc_error}")
                        raise Exception(f"Cannot create dispatch rule without agent config: {rc_error}")
                
                if not room_config_obj:
                    raise Exception("RoomConfiguration with agent is required but could not be created")
                
                # Build individual dispatch rule
                individual_rule = api.SIPDispatchRuleIndividual(room_prefix=EXPECTED_ROOM_PREFIX)
                rule_obj = api.SIPDispatchRule(dispatch_rule_individual=individual_rule)
                
                # Get all existing trunks to include in the global rule
                # This ensures the rule handles all phone numbers from the start
                all_trunk_ids = [trunk_id]  # Start with this trunk
                try:
                    if hasattr(livekit_api.sip, "list_sip_inbound_trunk"):
                        trunks_response = await livekit_api.sip.list_sip_inbound_trunk(api.ListSIPInboundTrunkRequest())
                        existing_trunks = trunks_response.items if hasattr(trunks_response, "items") else trunks_response or []
                        for existing_trunk in existing_trunks:
                            existing_trunk_id = (
                                getattr(existing_trunk, "sip_trunk_id", None)
                                or getattr(existing_trunk, "inbound_trunk_id", None)
                                or getattr(existing_trunk, "trunk_id", None)
                                or getattr(existing_trunk, "id", None)
                            )
                            if existing_trunk_id and existing_trunk_id not in all_trunk_ids:
                                all_trunk_ids.append(existing_trunk_id)
                        logger.info(f"[DISPATCH] Including {len(all_trunk_ids)} trunk(s) in global rule: {all_trunk_ids}")
                except Exception as list_error:
                    logger.warning(f"[DISPATCH] Could not list existing trunks, using only new trunk: {list_error}")
                
                # Create global rule with all trunks
                create_req = api.CreateSIPDispatchRuleRequest(
                    name=rule_name,
                    rule=rule_obj,
                    room_config=room_config_obj,
                    trunk_ids=all_trunk_ids  # Include all existing trunks + new one
                )
                
                logger.debug(f"[DISPATCH] Creating global dispatch rule with agent '{agent_name}'")
                try:
                    created = await livekit_api.sip.create_sip_dispatch_rule(create_req)
                    rule_id = (
                        getattr(created, "sip_dispatch_rule_id", None)
                        or getattr(created, "dispatch_rule_id", None)
                        or getattr(created, "id", None)
                    )
                    
                    if rule_id:
                        logger.info(f"[DISPATCH] ✓ Created global dispatch rule {rule_id} WITH agent '{agent_name}' configuration")
                        logger.info(f"[DISPATCH] Rule ready: agent={agent_name}, trunk={trunk_id} (will add more trunks as needed)")
                        return rule_id
                    else:
                        logger.error("[DISPATCH] Failed to extract rule_id from creation response")
                        return None
                except Exception as create_error:
                    error_msg = str(create_error)
                    # Check if it's a conflict error - trunk already in another rule
                    if "Conflicting SIP Dispatch Rules" in error_msg or "conflict" in error_msg.lower():
                        logger.warning(f"[DISPATCH] Conflict detected during creation: {error_msg}")
                        logger.info(f"[DISPATCH] Re-checking existing rules for trunk {trunk_id}...")
                        # Re-fetch rules and find the one with this trunk
                        response = await livekit_api.sip.list_sip_dispatch_rule(api.ListSIPDispatchRuleRequest())
                        rules = response.items if hasattr(response, "items") else response or []
                        for rule in rules:
                            current_trunk_ids = list(getattr(rule, "trunk_ids", []) or [])
                            if trunk_id in current_trunk_ids:
                                rule_id = (
                                    getattr(rule, "sip_dispatch_rule_id", None)
                                    or getattr(rule, "dispatch_rule_id", None)
                                    or getattr(rule, "id", None)
                                )
                                rule_name_attr = getattr(rule, "name", None) or "unnamed"
                                logger.info(f"[DISPATCH] ✓ Found conflicting rule {rule_id} (name: {rule_name_attr}) - reusing it")
                                return rule_id
                        logger.error(f"[DISPATCH] Conflict detected but could not find rule with trunk {trunk_id}")
                        return None
                    else:
                        # Re-raise if it's not a conflict error
                        raise
            
            # 5️⃣ Rule already exists - verify trunk is attached
            rule_id = (
                getattr(target_rule, "sip_dispatch_rule_id", None)
                or getattr(target_rule, "dispatch_rule_id", None)
                or getattr(target_rule, "id", None)
            )
            
            if not rule_id:
                logger.error("[DISPATCH] Found rule but could not extract rule_id")
                return None
            
            # Check if trunk is already attached
            current_trunk_ids = list(getattr(target_rule, "trunk_ids", []) or [])
            
            if trunk_id in current_trunk_ids:
                logger.info(f"[DISPATCH] ✓ Trunk {trunk_id} already attached to global rule {rule_id}")
                logger.info(f"[DISPATCH] Rule ready: phone={phone_number}, agent={agent_name}, trunk={trunk_id}")
                return rule_id
            
            # Trunk not attached - try to update the rule to add this trunk
            logger.info(f"[DISPATCH] Adding trunk {trunk_id} to global rule {rule_id} (current trunks: {current_trunk_ids})")
            
            # Try to update the rule with the new trunk ID
            try:
                if hasattr(livekit_api.sip, "update_sip_dispatch_rule"):
                    # Add new trunk to existing list
                    updated_trunk_ids = list(set(current_trunk_ids + [trunk_id]))  # Remove duplicates
                    
                    # Try to update with new trunk list
                    try:
                        update_req = api.UpdateSIPDispatchRuleRequest(
                            sip_dispatch_rule_id=rule_id,
                            trunk_ids=updated_trunk_ids
                        )
                        await livekit_api.sip.update_sip_dispatch_rule(update_req)
                        logger.info(f"[DISPATCH] ✓ Updated global rule {rule_id} with trunk {trunk_id} (total trunks: {len(updated_trunk_ids)})")
                        return rule_id
                    except (TypeError, AttributeError) as update_error:
                        logger.debug(f"[DISPATCH] Update with trunk_ids failed: {update_error}, trying alternative...")
                        # Try alternative parameter name
                        try:
                            update_req = api.UpdateSIPDispatchRuleRequest(
                                sip_dispatch_rule_id=rule_id
                            )
                            # Try setting trunk_ids as attribute
                            setattr(update_req, "trunk_ids", updated_trunk_ids)
                            await livekit_api.sip.update_sip_dispatch_rule(update_req)
                            logger.info(f"[DISPATCH] ✓ Updated global rule {rule_id} with trunk {trunk_id}")
                            return rule_id
                        except Exception as alt_error:
                            logger.warning(f"[DISPATCH] Could not update rule with new trunk: {alt_error}")
                else:
                    logger.warning(f"[DISPATCH] Update API not available, trunk {trunk_id} not added to rule")
            except Exception as update_error:
                logger.warning(f"[DISPATCH] Failed to update rule with trunk {trunk_id}: {update_error}")
            
            # If update failed, log warning but return rule_id anyway
            # The rule exists and may still work for other trunks
            logger.warning(
                f"[DISPATCH] Rule {rule_id} exists but trunk {trunk_id} could not be added. "
                f"Current trunks: {current_trunk_ids}. "
                f"LiveKit API may not support updating trunk_ids. "
                f"Rule will work for existing trunks, but this trunk may need manual configuration."
            )
            
            logger.info(f"[DISPATCH] Using existing global rule {rule_id} for phone={phone_number}")
            return rule_id

        except Exception as e:
            logger.error(f"[DISPATCH] Failed to attach trunk to dispatch rule: {e}", exc_info=True)
            return None

    async def _delete_dispatch_rule_for_phone(self, phone_number: str) -> bool:
        """
        Delete the dispatch rule for a phone number during cleanup.
        
        Since we have one dispatch rule per phone number, when unassigning
        we delete the entire rule rather than trying to update trunk_ids
        (which the LiveKit API doesn't support).
        
        Returns True if successful or rule doesn't exist, False on error.
        """
        try:
            livekit_api = self._get_livekit_api()
            normalized_phone = phone_number.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
            rule_name = f"dispatch-rule-{normalized_phone}"
            
            # Find the rule by name
            if not hasattr(livekit_api.sip, "list_sip_dispatch_rule"):
                logger.warning("[CLEANUP] Cannot list dispatch rules")
                return True
            
            response = await livekit_api.sip.list_sip_dispatch_rule(api.ListSIPDispatchRuleRequest())
            rules = response.items if hasattr(response, "items") else response or []
            
            target_rule = None
            for rule in rules:
                rule_name_attr = getattr(rule, "name", None)
                if rule_name_attr == rule_name:
                    target_rule = rule
                    break
            
            if not target_rule:
                logger.info(f"[CLEANUP] No dispatch rule found for {phone_number} during cleanup")
                return True  # Nothing to delete
            
            rule_id = (
                getattr(target_rule, "sip_dispatch_rule_id", None)
                or getattr(target_rule, "dispatch_rule_id", None)
                or getattr(target_rule, "id", None)
            )
            
            if not rule_id:
                logger.warning("[CLEANUP] Found rule but could not extract rule_id")
                return True
            
            # Delete the dispatch rule
            logger.info(f"[CLEANUP] Deleting dispatch rule {rule_id} for {phone_number}")
            
            if hasattr(livekit_api.sip, "delete_sip_dispatch_rule"):
                try:
                    await livekit_api.sip.delete_sip_dispatch_rule(
                        api.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule_id)
                    )
                    logger.info(f"[CLEANUP] ✓ Deleted dispatch rule {rule_id}")
                except Exception as delete_error:
                    logger.warning(f"[CLEANUP] Could not delete dispatch rule: {delete_error}")
                    # Non-critical - continue cleanup
            else:
                logger.warning(f"[CLEANUP] delete_sip_dispatch_rule not available, rule {rule_id} remains")
            
            return True

        except Exception as e:
            logger.error(f"[CLEANUP] Failed to delete dispatch rule: {e}", exc_info=True)
            return False

    async def _configure_twilio_phone_webhook(self, tenant_id: str, phone_number: str) -> Dict[str, Any]:
        """
        Configure Twilio phone number webhook URLs.

        Returns phone number resource info.
        """
        try:
            twilio_integration = await self._get_decrypted_twilio_integration(tenant_id)
            if not twilio_integration:
                raise ValueError(f"No Twilio integration found for tenant {tenant_id}")

            from app.core.config import TWILIO_WEBHOOK_BASE_URL

            if not TWILIO_WEBHOOK_BASE_URL:
                raise ValueError(
                    "TWILIO_WEBHOOK_BASE_URL environment variable is required for webhook configuration."
                )

            base_url = TWILIO_WEBHOOK_BASE_URL.rstrip("/")
            webhook_url = f"{base_url}/api/v1/voice-agent/twilio/webhook"
            status_callback_url = f"{base_url}/api/v1/voice-agent/twilio/status"

            logger.info(f"[TWILIO] Configuring webhooks - Voice: {webhook_url}, Status: {status_callback_url}")

            normalized_phone = normalize_phone_number(phone_number)

            def _configure_webhook_sync():
                twilio_client = Client(twilio_integration["account_sid"], twilio_integration["auth_token"])

                incoming_numbers = twilio_client.incoming_phone_numbers.list(phone_number=normalized_phone)

                if not incoming_numbers:
                    logger.warning(f"Phone number {normalized_phone} not found, trying original format")
                    incoming_numbers = twilio_client.incoming_phone_numbers.list(phone_number=phone_number)

                if not incoming_numbers:
                    raise ValueError(f"Phone number {normalized_phone} not found in Twilio account.")

                phone_number_resource = incoming_numbers[0]
                logger.info(f"[TWILIO] Found phone number: SID={phone_number_resource.sid}")

                phone_number_resource.update(
                    voice_url=webhook_url,
                    voice_method="POST",
                    status_callback=status_callback_url,
                    status_callback_method="POST",
                )

                return phone_number_resource

            phone_number_resource = await _run_twilio_sync(_configure_webhook_sync)

            logger.info(f"[TWILIO] ✓ Successfully configured webhooks for {normalized_phone}")

            return {
                "phone_sid": phone_number_resource.sid,
                "phone_number": phone_number_resource.phone_number,
                "voice_url": webhook_url,
                "status_callback": status_callback_url,
            }

        except TwilioException as e:
            logger.error(f"Twilio API error: {e}", exc_info=True)
            raise Exception(f"Twilio API error: {str(e)}")
        except ValueError as e:
            logger.error(f"Validation error: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to configure phone number webhook: {e}", exc_info=True)
            raise Exception(f"Phone number webhook configuration failed: {str(e)}")

    async def setup_phone_number_sip(self, tenant_id: str, phone_number: str, agent_id: str) -> Dict[str, Any]:
        """
        Setup complete SIP infrastructure for phone number assignment.

        This method:
        1. Creates/verifies LiveKit SIP inbound trunk for the phone number
        2. Attaches the trunk to the voice-agent dispatch rule
        3. Configures Twilio phone number webhook

        Returns configuration details including:
        - trunk_id: The SIP trunk ID
        - dispatch_rule_validated: Boolean indicating if trunk was attached to dispatch rule
        - phone_config: Twilio webhook configuration
        - sip_domain: The LiveKit SIP domain
        """
        try:
            logger.info(f"[SIP SETUP] Starting for phone={phone_number}, agent={agent_id}, tenant={tenant_id}")

            # Step 1: Get or create SIP trunk for this phone number
            # NOTE: Trunk does NOT contain tenant_id - multi-tenant routing via SIP headers
            # CRITICAL: Must have a valid trunk ID - no fallback to string IDs
            trunk_id = await self._get_or_create_sip_trunk(phone_number)
            logger.info(f"[SIP SETUP] ✓ SIP trunk ready: {trunk_id}")

            # Step 2: Attach trunk to dispatch rule (auto-create if needed)
            normalized_phone = normalize_phone_number_safe(phone_number) or phone_number
            dispatch_rule_id = None
            try:
                livekit_api = self._get_livekit_api()
                dispatch_rule_id = await self._attach_trunk_to_dispatch_rule(
                    livekit_api, trunk_id=trunk_id, phone_number=normalized_phone
                )
                if dispatch_rule_id:
                    logger.info(f"[SIP SETUP] ✓ Trunk attached to dispatch rule {dispatch_rule_id}")
                else:
                    logger.warning("[SIP SETUP] ✗ Failed to attach trunk to dispatch rule")
            except Exception as dispatch_error:
                logger.warning(f"[SIP SETUP] Failed to setup dispatch rule: {dispatch_error}")

            # Step 3: Configure Twilio phone number webhook
            phone_config = await self._configure_twilio_phone_webhook(tenant_id, phone_number)
            logger.info(f"[SIP SETUP] ✓ Twilio webhook configured: {phone_config['phone_sid']}")

            # Return dispatch_rule_id (real ID, not boolean)
            return {
                "trunk_id": trunk_id,
                "dispatch_rule_id": dispatch_rule_id,  # Real dispatch rule ID
                "phone_config": phone_config,
                "sip_domain": LIVEKIT_SIP_DOMAIN,
                "status": "configured",
            }

        except Exception as e:
            logger.error(f"[SIP SETUP] Failed: {e}", exc_info=True)
            raise Exception(f"SIP configuration failed: {str(e)}")

    async def cleanup_phone_number_sip(
        self, tenant_id: str, phone_number: str, dispatch_rule_id: Optional[str] = None, trunk_id: Optional[str] = None
    ) -> bool:
        """
        Cleanup SIP configuration when phone number is unassigned.

        NOTE: We do NOT delete trunks or dispatch rules anymore.
        - Trunks remain (one per phone number, can be reused)
        - Global dispatch rule remains (handles all phone numbers)
        - Trunk IDs are not removed from dispatch rule (LiveKit API limitation)
        
        This is a no-op cleanup - resources remain for potential reuse.
        """
        try:
            logger.info(f"[CLEANUP] Phone number {phone_number} unassigned - trunks and dispatch rule remain for reuse")
            logger.info("[CLEANUP] ✓ Cleanup completed (no deletion - resources preserved)")
            return True

        except Exception as e:
            logger.error(f"[CLEANUP] Failed: {e}")
            return False


# Global SIP configuration service instance
sip_configuration_service = SIPConfigurationService()
