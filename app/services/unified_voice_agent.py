"""
Unified LiveKit Voice Agent Service
====================================
Handles both browser-based testing and Twilio phone call integration.

ARCHITECTURE (LiveKit Official Telephony Flow):
===============================================
INBOUND CALL FLOW:
1. Twilio PSTN â†’ Backend webhook (/twilio/webhook)
2. Backend identifies tenant from called phone number (To)
3. Backend generates unique call_id for this call
4. Backend stores config under: tenant_config:<tenant_id>:<call_id>
5. Backend returns TwiML <Dial><Sip> to LiveKit SIP domain
   - Passes tenant_id and call_id via SIP headers
   - Does NOT specify room name (LiveKit creates it)
6. LiveKit SIP inbound trunk receives INVITE
7. SIP Dispatch Rule creates auto-named room: call-+14438600638_x9fhA2Lt
8. Dispatch Rule launches voice-agent worker
9. Worker extracts tenant_id + call_id from SIP headers
10. Worker loads config from Redis: tenant_config:<tenant_id>:<call_id>
11. Worker handles call with tenant-specific configuration

KEY PRINCIPLES:
- Backend NEVER creates LiveKit rooms for inbound calls
- Backend NEVER depends on or generates room names
- Backend stores config keyed by tenant_id + call_id (NOT room name)
- Multiple tenants CAN share the same PSTN number
- Each call gets isolated config via unique call_id
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from livekit import api
from twilio.rest import Client
from twilio.twiml.voice_response import Dial, VoiceResponse

from app.core.async_redis import async_redis_client
from app.core.config import (
    CALL_CONFIG_TTL_SECONDS,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_SIP_DOMAIN,
    LIVEKIT_SIP_HEADER_TENANT_ID,
    LIVEKIT_SIP_HEADER_CALL_ID,
    LIVEKIT_SIP_HEADER_CALLED_NUMBER,
    LIVEKIT_URL,
    TWILIO_WEBHOOK_BASE_URL,
)
from app.core.encryption import encryption_service
from app.core.utils import get_current_timestamp
from app.services.store import store
from app.utils.phone_number import normalize_phone_number_safe

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class UnifiedVoiceAgentService:
    """
    Unified voice agent service that handles both:
    1. Browser-based testing (direct LiveKit connection)
    2. Phone calls (Twilio SIP â†’ LiveKit bridge)

    IMPORTANT: For inbound phone calls, this service:
    - Does NOT create LiveKit rooms (dispatch rule creates them)
    - Does NOT depend on room names for config
    - Stores config by tenant_id + call_id for worker to retrieve
    """

    def __init__(self):
        """Initialize unified voice agent service."""
        self.tenant_agents = {}  # tenant_id:service_type -> agent_instance (for browser testing)
        self.active_sessions = {}  # session_id -> session_data (for tracking)
        self._livekit_api = None
        self._session_ttl_seconds = max(CALL_CONFIG_TTL_SECONDS, 24 * 60 * 60)

        # Validate LiveKit configuration at startup
        self._validate_livekit_config()

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"voice_session:{session_id}"

    @staticmethod
    def _tenant_session_set_key(tenant_id: str) -> str:
        return f"voice_tenant_sessions:{tenant_id}"

    @staticmethod
    def _callsid_session_key(twilio_call_sid: str) -> str:
        return f"voice_callsid_session:{twilio_call_sid}"

    async def _store_session(self, session_id: str, session_data: Dict[str, Any]) -> None:
        """Store session in Redis (authoritative) and mirror in memory as cache."""
        await async_redis_client.set_json(self._session_key(session_id), session_data, ttl=self._session_ttl_seconds)
        tenant_id = session_data.get("tenant_id")
        if tenant_id:
            tenant_set = self._tenant_session_set_key(str(tenant_id))
            await async_redis_client.sadd(tenant_set, session_id)
            await async_redis_client.expire(tenant_set, self._session_ttl_seconds)
        twilio_call_sid = session_data.get("twilio_call_sid")
        if twilio_call_sid:
            await async_redis_client.set(
                self._callsid_session_key(str(twilio_call_sid)), session_id, ttl=self._session_ttl_seconds
            )
        self.active_sessions[session_id] = session_data

    async def _get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session from Redis first, fallback to memory cache."""
        data = await async_redis_client.get_json(self._session_key(session_id))
        if data:
            self.active_sessions[session_id] = data
            return data
        return self.active_sessions.get(session_id)

    async def _update_session(self, session_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Patch and persist a session."""
        existing = await self._get_session(session_id)
        if not existing:
            return None
        merged = {**existing, **updates}
        await self._store_session(session_id, merged)
        return merged

    async def _delete_session(self, session_id: str) -> None:
        """Delete session and secondary indexes from Redis and memory."""
        existing = await self._get_session(session_id)
        await async_redis_client.delete(self._session_key(session_id))
        if existing:
            tenant_id = existing.get("tenant_id")
            if tenant_id:
                await async_redis_client.srem(self._tenant_session_set_key(str(tenant_id)), session_id)
            twilio_call_sid = existing.get("twilio_call_sid")
            if twilio_call_sid:
                await async_redis_client.delete(self._callsid_session_key(str(twilio_call_sid)))
        self.active_sessions.pop(session_id, None)

    async def _find_session_by_twilio_call_sid(self, call_sid: Optional[str]) -> Optional[Dict[str, Any]]:
        if not call_sid:
            return None
        mapped_session_id = await async_redis_client.get(self._callsid_session_key(call_sid))
        if mapped_session_id:
            return await self._get_session(mapped_session_id)
        for _, sess_data in self.active_sessions.items():
            if sess_data.get("twilio_call_sid") == call_sid:
                return sess_data
        return None

    async def list_tenant_sessions(self, tenant_id: str, active_only: bool = True) -> List[Dict[str, Any]]:
        """List tenant sessions from Redis-backed session store."""
        session_ids = await async_redis_client.smembers(self._tenant_session_set_key(tenant_id))
        sessions: List[Dict[str, Any]] = []
        for session_id in session_ids:
            session = await self._get_session(session_id)
            if not session:
                continue
            if active_only and session.get("status") == "ended":
                continue
            sessions.append({"session_id": session_id, **session})
        sessions.sort(key=lambda row: row.get("created_at") or "", reverse=True)
        return sessions

    async def get_tenant_agent_stats(self, tenant_id: str) -> Dict[str, Any]:
        """Aggregate tenant stats from Redis-backed sessions + in-memory agent cache."""
        agent_instances = []
        for key, agent_data in self.tenant_agents.items():
            if agent_data["tenant_id"] == tenant_id:
                created_at = agent_data.get("created_at")
                agent_instances.append(
                    {
                        "service_type": agent_data["service_type"],
                        "created_at": created_at if isinstance(created_at, str) else created_at.isoformat(),
                    }
                )

        all_sessions = await self.list_tenant_sessions(tenant_id, active_only=False)
        active_count = len([s for s in all_sessions if s.get("status") != "ended"])

        return {
            "tenant_id": tenant_id,
            "agent_instances": agent_instances,
            "agent_count": len(agent_instances),
            "active_sessions": active_count,
            "total_sessions": len(all_sessions),
        }

    def _validate_livekit_config(self):
        """Validate LiveKit configuration at startup."""
        logger.info("=" * 60)
        logger.info("VALIDATING LIVEKIT CONFIGURATION")
        logger.info("=" * 60)
        logger.info(f"LIVEKIT_URL: '{LIVEKIT_URL}'")
        logger.info(f"LIVEKIT_API_KEY: {'âœ“ SET' if LIVEKIT_API_KEY else 'âœ— NOT SET'}")
        logger.info(f"LIVEKIT_API_SECRET: {'âœ“ SET' if LIVEKIT_API_SECRET else 'âœ— NOT SET'}")
        logger.info(f"LIVEKIT_SIP_DOMAIN: '{LIVEKIT_SIP_DOMAIN}'")

        if not LIVEKIT_URL or LIVEKIT_URL.strip() == "":
            logger.error("âš ï¸ CRITICAL: LIVEKIT_URL is not configured!")
            logger.error("Please set LIVEKIT_URL in your .env file")
        else:
            logger.info("âœ“ LIVEKIT_URL is configured")

        if not LIVEKIT_SIP_DOMAIN or LIVEKIT_SIP_DOMAIN.strip() == "":
            logger.error("âš ï¸ CRITICAL: LIVEKIT_SIP_DOMAIN is not configured!")
            logger.error("Please set LIVEKIT_SIP_DOMAIN in your .env file")
        else:
            logger.info("âœ“ LIVEKIT_SIP_DOMAIN is configured")

        logger.info("=" * 60)

    @property
    def livekit_api(self):
        """Get LiveKit API instance (lazy initialization)."""
        if self._livekit_api is None:
            if not LIVEKIT_URL:
                raise ValueError("LIVEKIT_URL is not configured")
            self._livekit_api = api.LiveKitAPI(url=LIVEKIT_URL, api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
        return self._livekit_api

    @staticmethod
    def _resolve_agent_type(agent_data: Optional[Dict[str, Any]]) -> str:
        """Resolve agent type with migration fallback."""
        if not agent_data:
            return "voice"
        return agent_data.get("agent_type") or "voice"

    async def start_session(
        self,
        tenant_id: str,
        service_type: str,
        test_mode: bool = True,
        phone_number: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Start a voice agent session.

        For test_mode=True: Creates token with RoomConfiguration; room is created on join
        For test_mode=False: Initiates outbound Twilio call (room still pre-created for outbound)
        
        NOTE: For INBOUND calls, the room is created by LiveKit dispatch rule, not here.
        """
        try:
            session_id = str(uuid.uuid4())
            
            # For browser testing, we do NOT pre-create rooms.
            # Rooms are created on first join using token RoomConfiguration.
            # For outbound calls, we still create rooms (different from inbound).
            room_name = f"agent-{tenant_id}-{session_id[:8]}"

            logger.info(f"Starting session {session_id} for tenant {tenant_id}, test_mode={test_mode}")

            # Extract agent_id from metadata if provided
            agent_id = None
            voice_id = None
            agent_data = None

            if metadata and "agent_id" in metadata:
                agent_id = metadata["agent_id"]
                logger.info(f"Using agent_id from metadata: {agent_id}")

                try:
                    agent_data = await store.get_agent(agent_id)
                    if not agent_data:
                        raise ValueError(f"Voice agent '{agent_id}' not found")

                    agent_type = self._resolve_agent_type(agent_data)
                    if agent_type != "voice":
                        raise ValueError(
                            f"Agent {agent_id} is type '{agent_type}'. Voice sessions require voice agents."
                        )
                    voice_id = agent_data.get("voice_id")
                    logger.info(f"Fetched agent config - voice_id: {voice_id}, name: {agent_data.get('name')}")
                except Exception as e:
                    logger.error(f"Error fetching agent data: {e}")
                    raise

            # Store session data
            session_data = {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "service_type": service_type,
                "room_name": room_name,
                "test_mode": test_mode,
                "phone_number": phone_number,
                "status": "initializing",
                "metadata": metadata or {},
                "created_at": get_current_timestamp(),
                "started_at": None,
                "ended_at": None,
            }

            if test_mode:
                # BROWSER TESTING MODE: token-based dispatch on participant connection
                logger.info(f"Preparing browser test room (token dispatch; room created on join): {room_name}")
                
                # Create room config for browser testing
                room_config = {
                    "tenant_id": tenant_id,
                    "service_type": service_type,
                    "agent_id": agent_id,
                    "voice_id": voice_id,
                    "agent_data": self._clean_agent_data(agent_data) if agent_data else None,
                    "call_id": session_id,  # Use session_id as call_id for browser tests
                    "session_id": session_id,  # Also store session_id for reference
                }
                
                # For browser testing, store config under tenant_config for worker
                await self._store_tenant_config(tenant_id, session_id, room_config)
                
                # ALSO store by room name for direct lookup by worker
                await self._store_room_config(room_name, room_config)
                
                # Do NOT pre-create the room. When the participant joins with a token that
                # includes RoomConfiguration, LiveKit creates the room and dispatches the agent.
                # Pre-creating the room would skip token room_config and prevent dispatch.
                logger.info(f"[ROOM] Skipping pre-create for browser testing: {room_name}")
                
                # Generate token for browser client
                token = self._generate_room_token(room_name, f"client-{session_id[:8]}")
                session_data["status"] = "ready"
                session_data["connection_type"] = "browser"

                livekit_url = self._format_livekit_url()

                result = {
                    "session_id": session_id,
                    "room_name": room_name,
                    "token": token,
                    "livekit_url": livekit_url,
                    "status": "ready",
                    "message": "Connect your browser to the LiveKit room using the provided token",
                }
            else:
                # OUTBOUND CALL MODE: Initiate Twilio call
                if not phone_number:
                    raise ValueError("phone_number is required when test_mode=False")

                # For outbound, we still create room and initiate call
                call_sid = await self._initiate_twilio_call(
                    tenant_id=tenant_id, phone_number=phone_number, room_name=room_name
                )

                session_data["status"] = "calling"
                session_data["connection_type"] = "phone"
                session_data["twilio_call_sid"] = call_sid

                result = {
                    "session_id": session_id,
                    "room_name": room_name,
                    "status": "calling",
                    "twilio_call_sid": call_sid,
                    "phone_number": phone_number,
                    "message": f"Calling {phone_number}...",
                }

            await self._store_session(session_id, session_data)
            return result

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error starting session: {e}")
            raise Exception(f"Failed to start session: {str(e)}")

    async def end_session(self, session_id: str) -> bool:
        """End a voice agent session."""
        try:
            session = await self._get_session(session_id)
            if not session:
                logger.warning(f"Session {session_id} not found")
                return False

            room_name = session.get("room_name")

            logger.info(f"Ending session {session_id}")

            session = await self._update_session(
                session_id, {"status": "ended", "ended_at": get_current_timestamp()}
            ) or session

            # Delete LiveKit room if it exists
            if room_name:
                await self._delete_room(room_name)

            # If it's a phone call, hang up Twilio call
            if not session["test_mode"] and "twilio_call_sid" in session:
                await self._hangup_twilio_call(session["tenant_id"], session["twilio_call_sid"])

            await self._delete_session(session_id)
            return True

        except Exception as e:
            logger.error(f"Error ending session: {e}")
            return False

    async def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session status."""
        return await self._get_session(session_id)

    async def clear_agent_cache(self, tenant_id: Optional[str] = None, agent_id: Optional[str] = None) -> Dict[str, Any]:
        """Clear cached agent instances."""
        cleared = 0

        if agent_id and tenant_id:
            keys_to_remove = [key for key in self.tenant_agents.keys() if agent_id in key and tenant_id in key]
        elif tenant_id:
            keys_to_remove = [key for key in self.tenant_agents.keys() if tenant_id in key]
        else:
            keys_to_remove = list(self.tenant_agents.keys())

        for key in keys_to_remove:
            del self.tenant_agents[key]
            cleared += 1

        logger.info(f"Cleared {cleared} agent instance(s) from cache")
        return {"cleared": cleared, "message": f"Successfully cleared {cleared} agent instance(s)"}

    async def handle_twilio_webhook(self, form_data: Dict[str, Any]) -> VoiceResponse:
        """
        Handle incoming Twilio webhook for INBOUND calls.
        
        CANONICAL FLOW:
        1. Extract call information from Twilio
        2. Lookup agent by phone number (To)
        3. Generate unique call_id for this call
        4. Store config under tenant_config:<tenant_id>:<call_id>
        5. Return TwiML that dials LiveKit SIP domain
           - Pass tenant_id and call_id via SIP headers
           - Do NOT specify room name (LiveKit creates it)
        
        IMPORTANT: We do NOT create LiveKit rooms here.
        The SIP dispatch rule creates the room automatically.
        """
        try:
            # Extract call information
            call_sid = form_data.get("CallSid")
            from_number = form_data.get("From")
            to_number = form_data.get("To")

            logger.info(f"[TWILIO WEBHOOK] Inbound call: CallSid={call_sid}, From={from_number}, To={to_number}")

            # Check if this is an outbound call we initiated (session already exists)
            existing_session = await self._find_session_by_twilio_call_sid(call_sid)

            if existing_session:
                # OUTBOUND CALL: Use existing session (room was pre-created)
                logger.info(f"[TWILIO WEBHOOK] Found existing session for outbound call {call_sid}")
                room_name = existing_session["room_name"]
                
                # For outbound, use room-based SIP URI
                sip_uri = self._get_livekit_sip_uri_with_room(room_name)
                
                response = VoiceResponse()
                dial = Dial()
                dial.sip(sip_uri)
                response.append(dial)
                
                await self._update_session(
                    existing_session.get("session_id", ""),
                    {"status": "connecting", "started_at": get_current_timestamp()},
                )
                return response

            # ========================================
            # INBOUND CALL HANDLING (NEW ARCHITECTURE)
            # ========================================
            logger.info(f"[TWILIO WEBHOOK] Processing as INBOUND call to {to_number}")

            # Step 1: Normalize phone number
            normalized_to_number = normalize_phone_number_safe(to_number)
            if not normalized_to_number:
                logger.error(f"[TWILIO WEBHOOK] Invalid phone number format: {to_number}")
                return self._error_response("Invalid phone number format. Please contact support.")

            logger.info(f"[TWILIO WEBHOOK] Normalized phone number: {to_number} -> {normalized_to_number}")

            # Step 2: Lookup phone number assignment
            phone_record = await store.get_phone_by_number(normalized_to_number)
            if not phone_record:
                phone_record = await store.get_phone_by_number(to_number)

            if not phone_record:
                logger.error(f"[TWILIO WEBHOOK] Phone number {normalized_to_number} not found in database")
                return self._error_response("This number is not configured. Please contact support.")

            logger.info(f"[TWILIO WEBHOOK] âœ“ Found phone record: ID={phone_record.get('id')}, AgentID={phone_record.get('agent_id')}")

            # Step 3: Validate phone record has required fields
            agent_id = phone_record.get("agent_id")
            tenant_id = phone_record.get("tenant_id")

            if not agent_id:
                logger.error("[TWILIO WEBHOOK] Phone number has no agent_id assigned")
                return self._error_response("This number is not assigned to an agent. Please contact support.")

            if not tenant_id:
                logger.error("[TWILIO WEBHOOK] Phone number has no tenant_id")
                return self._error_response("Configuration error. Please contact support.")

            # Step 4: Get agent configuration
            agent = await store.get_agent(agent_id)
            if not agent:
                logger.error(f"[TWILIO WEBHOOK] Agent {agent_id} not found")
                return self._error_response("Agent configuration not found. Please contact support.")

            logger.info(f"[TWILIO WEBHOOK] âœ“ Found agent: Name={agent.get('name')}, ServiceType={agent.get('service_type')}")

            # Voice safety: inbound phone calls can only target voice agents.
            # Legacy agents without an explicit type are treated as voice.
            agent_type = self._resolve_agent_type(agent)
            if agent_type != "voice":
                logger.error(f"[TWILIO WEBHOOK] Agent {agent_id} has type={agent_type} - FAILING CALL")
                return self._error_response("This number is assigned to a non-voice agent. Please contact support.")

            # Step 5: Validate agent has service_type (NO FALLBACK)
            service_type = agent.get("service_type")
            if not service_type:
                logger.error(f"[TWILIO WEBHOOK] Agent {agent_id} has no service_type - FAILING CALL")
                return self._error_response("Agent configuration is incomplete. Please contact support.")

            # Step 6: Generate unique call_id for this call
            call_id = str(uuid.uuid4())[:12]
            logger.info(f"[TWILIO WEBHOOK] Generated call_id: {call_id}")

            # Step 7: Prepare tenant config for worker
            config_data = {
                "agent_id": agent_id,
                "tenant_id": tenant_id,
                "call_id": call_id,
                "agent_data": self._clean_agent_data(agent),
                "voice_id": agent.get("voice_id"),
                "agent_type": agent_type,
                "service_type": service_type,
                "call_type": "inbound",
                "caller_number": from_number,
                "called_number": normalized_to_number,
                "twilio_call_sid": call_sid,
                "created_at": get_current_timestamp(),
            }

            # Step 8: Store config in Redis for worker to retrieve
            # IMPORTANT: Store by TWILIO CALL SID because that's what the worker can reliably read
            # from sip.twilio.callSid attribute (custom SIP headers via query params don't work)
            await self._store_config_by_twilio_callsid(call_sid, config_data)
            
            # Also store by tenant+call_id for backward compatibility and cleanup
            stored = await self._store_tenant_config(tenant_id, call_id, config_data)
            if not stored:
                logger.error("[TWILIO WEBHOOK] Failed to store tenant config")
                return self._error_response("Unable to initialize call. Please try again later.")

            logger.info(f"[TWILIO WEBHOOK] âœ“ Stored config: call_config:{call_sid}")

            # Step 9 & 10: Build SIP URI WITH original Twilio CallSid
            # CRITICAL: Twilio creates a NEW CallSid when dialing LiveKit via SIP
            # We must pass our original CallSid as x-lk-callid query param
            sip_uri = self._build_sip_uri_with_callsid(call_sid, tenant_id, normalized_to_number)
            
            logger.info(f"[TWILIO WEBHOOK] Complete SIP URI: {sip_uri}")

            # Step 11: Build TwiML response
            response = VoiceResponse()
            dial = Dial()
            dial.sip(sip_uri)
            response.append(dial)

            # Step 12: Track session for status callbacks (Redis-backed store)
            session_id = f"inbound-{call_sid}"
            await self._store_session(
                session_id,
                {
                "session_id": session_id,
                "room_name": None,  # Room is created by LiveKit, not us
                "agent_id": agent_id,
                "tenant_id": tenant_id,
                "call_id": call_id,
                "twilio_call_sid": call_sid,
                "status": "connecting",
                "call_type": "inbound",
                "from_number": from_number,
                "to_number": to_number,
                "created_at": get_current_timestamp(),
                "started_at": get_current_timestamp(),
                },
            )

            logger.info(f"[TWILIO WEBHOOK] âœ“ Inbound call setup complete: tenant={tenant_id}, agent={agent.get('name')}")
            return response

        except Exception as e:
            logger.error(f"[TWILIO WEBHOOK] Error: {e}", exc_info=True)
            return self._error_response("An error occurred. Please try again later.")

    async def handle_twilio_status_callback(self, form_data: Dict[str, Any]):
        """Handle Twilio call status updates."""
        try:
            call_sid = form_data.get("CallSid")
            call_status = form_data.get("CallStatus")

            logger.info(f"[TWILIO STATUS] CallSid={call_sid}, Status={call_status}")

            session = await self._find_session_by_twilio_call_sid(call_sid)
            if not session:
                return

            session_id = session.get("session_id")
            if not session_id:
                return

            updates: Dict[str, Any] = {"twilio_status": call_status}
            if call_status in ["completed", "failed", "busy", "no-answer"]:
                updates["status"] = "ended"
                updates["ended_at"] = get_current_timestamp()

            updated = await self._update_session(session_id, updates)
            if not updated:
                return

            if call_status in ["completed", "failed", "busy", "no-answer"]:
                # Cleanup: Delete configs from Redis
                tenant_id = updated.get("tenant_id")
                call_id = updated.get("call_id")
                twilio_call_sid = updated.get("twilio_call_sid")

                # Delete call_config (primary lookup key by Twilio CallSid)
                if twilio_call_sid:
                    try:
                        call_config_key = f"call_config:{twilio_call_sid}"
                        await async_redis_client.delete(call_config_key)
                        logger.info(f"[CLEANUP] Deleted config: {call_config_key}")
                    except Exception as e:
                        logger.warning(f"[CLEANUP] Failed to delete call_config: {e}")

                # Delete tenant_config (backup key)
                if tenant_id and call_id:
                    try:
                        tenant_config_key = f"tenant_config:{tenant_id}:{call_id}"
                        await async_redis_client.delete(tenant_config_key)
                        logger.info(f"[CLEANUP] Deleted config: {tenant_config_key}")
                    except Exception as e:
                        logger.warning(f"[CLEANUP] Failed to delete tenant_config: {e}")

        except Exception as e:
            logger.error(f"[TWILIO STATUS] Error: {e}")

    # ========================================
    # PRIVATE HELPER METHODS
    # ========================================

    def _error_response(self, message: str) -> VoiceResponse:
        """Create an error TwiML response."""
        response = VoiceResponse()
        response.say(message)
        response.hangup()
        return response

    def _clean_agent_data(self, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Clean agent data for JSON serialization."""
        if not agent_data:
            return None
        return {
            "id": agent_data.get("id"),
            "agent_type": self._resolve_agent_type(agent_data),
            "name": agent_data.get("name"),
            "voice_id": agent_data.get("voice_id"),
            "language": agent_data.get("language"),
            "greeting_message": agent_data.get("greeting_message"),
            "service_type": agent_data.get("service_type"),
            "system_prompt": agent_data.get("system_prompt") or "",
            "tenant_id": agent_data.get("tenant_id"),
        }

    async def _store_tenant_config(
        self, tenant_id: str, call_id: str, config: Dict[str, Any], ttl: int = CALL_CONFIG_TTL_SECONDS
    ) -> bool:
        """
        Store tenant configuration in Redis for worker to retrieve.
        
        Key format: tenant_config:<tenant_id>:<call_id>
        
        This approach:
        - Supports multiple tenants sharing the same phone number
        - Provides per-call isolation via call_id
        - Does NOT depend on room name
        """
        try:
            config_key = f"tenant_config:{tenant_id}:{call_id}"
            config_json = json.dumps(config)
            
            logger.info(f"[REDIS STORE] Storing config with key: {config_key}")
            await async_redis_client.set(config_key, config_json, ttl=ttl)
            
            # Verify storage
            verify_data = await async_redis_client.get(config_key)
            if verify_data:
                logger.info("[REDIS STORE] âœ“ Successfully stored and verified config")
                return True
            else:
                logger.error("[REDIS STORE] âœ— Failed to verify config storage")
                return False
                
        except Exception as exc:
            logger.error(f"[REDIS STORE] âœ— Error storing config: {exc}", exc_info=True)
            return False

    async def _store_room_config(
        self, room_name: str, config: Dict[str, Any], ttl: int = CALL_CONFIG_TTL_SECONDS
    ) -> bool:
        """
        Store call configuration in Redis by room name (for browser test sessions).
        
        Key format: room_config:<room_name>
        
        This allows the worker to look up config directly from the room name
        when handling browser test sessions (WebRTC participants).
        """
        try:
            config_key = f"room_config:{room_name}"
            config_json = json.dumps(config)
            
            logger.info(f"[REDIS STORE] Storing config by room name: {config_key}")
            await async_redis_client.set(config_key, config_json, ttl=ttl)
            
            # Verify storage
            verify_data = await async_redis_client.get(config_key)
            if verify_data:
                logger.info("[REDIS STORE] âœ“ Successfully stored config by room name")
                return True
            else:
                logger.error("[REDIS STORE] âœ— Failed to verify config storage by room name")
                return False
                
        except Exception as exc:
            logger.error(f"[REDIS STORE] âœ— Error storing config by room name: {exc}", exc_info=True)
            return False

    async def _store_config_by_twilio_callsid(
        self, twilio_call_sid: str, config: Dict[str, Any], ttl: int = CALL_CONFIG_TTL_SECONDS
    ) -> bool:
        """
        Store call configuration in Redis by Twilio CallSid.
        
        Key format: call_config:<twilio_call_sid>
        
        This is the PRIMARY lookup method for the worker because:
        - sip.twilio.callSid is ALWAYS available in SIP participant attributes
        - Custom SIP headers via query params are NOT forwarded by Twilio to LiveKit
        
        The worker will use this key to retrieve the full config including tenant_id.
        """
        try:
            config_key = f"call_config:{twilio_call_sid}"
            config_json = json.dumps(config)
            
            logger.info(f"[REDIS STORE] Storing config by Twilio CallSid: {config_key}")
            await async_redis_client.set(config_key, config_json, ttl=ttl)
            
            # Verify storage
            verify_data = await async_redis_client.get(config_key)
            if verify_data:
                logger.info("[REDIS STORE] âœ“ Successfully stored config by Twilio CallSid")
                return True
            else:
                logger.error("[REDIS STORE] âœ— Failed to verify config storage by Twilio CallSid")
                return False
                
        except Exception as exc:
            logger.error(f"[REDIS STORE] âœ— Error storing config by Twilio CallSid: {exc}", exc_info=True)
            return False

    def _build_sip_uri_with_callsid(self, twilio_call_sid: str, tenant_id: str, called_number: str) -> str:
        """
        Build SIP URI with the original Twilio CallSid as query parameter.
        
        CRITICAL: Twilio creates a NEW CallSid when it dials LiveKit via SIP.
        So sip.twilio.callSid in the worker is DIFFERENT from the webhook CallSid!
        
        We must pass our original CallSid via x-lk-callid query param so the worker
        can read it from sip.h.x-lk-callid and match it with Redis.
        
        Format: sip:lk@domain?x-lk-callid=CA123&x-lk-tenantid=...&x-lk-callednumber=...
        
        LiveKit will expose these as participant attributes:
        - sip.h.x-lk-callid
        - sip.h.x-lk-tenantid
        - sip.h.x-lk-callednumber
        """
        from urllib.parse import urlencode
        
        domain = self._get_livekit_sip_domain()
        
        # Build query parameters with our metadata
        # These become X-LK-* SIP headers that are mapped via headers_to_attributes
        query_params = urlencode({
            "X-LK-CallId": twilio_call_sid,
            "X-LK-TenantId": tenant_id,
            "X-LK-CalledNumber": called_number,
        })
        
        sip_uri = f"sip:lk@{domain}?{query_params}"
        
        logger.info(f"[SIP URI] Built URI with CallSid: {sip_uri}")
        logger.info(f"[SIP URI] Included metadata: callid={twilio_call_sid}, tenant={tenant_id}, number={called_number}")
        return sip_uri

    def _get_livekit_sip_domain(self) -> str:
        """
        Get the cleaned LiveKit SIP domain.
        
        Returns just the domain without sip: prefix.
        """
        if not LIVEKIT_SIP_DOMAIN:
            raise ValueError("LIVEKIT_SIP_DOMAIN is not configured")
        
        domain = LIVEKIT_SIP_DOMAIN.strip()
        if domain.startswith("sip:"):
            domain = domain[4:].strip()
        
        return domain

    def _build_sip_query_params(self, tenant_id: str, call_id: str, called_number: str) -> str:
        """
        Build SIP query parameters string for Twilio to LiveKit routing.
        
        TWILIO OFFICIAL FORMAT: Custom SIP headers must be passed as query parameters.
        Format: ?header1=value1&header2=value2
        
        Twilio forwards these as SIP headers in the INVITE, and LiveKit maps them
        to participant attributes:
        - X-LK-TenantId -> sip.h.x-lk-tenantid (LiveKit normalizes to lowercase)
        - X-LK-CallId -> sip.h.x-lk-callid
        - X-LK-CalledNumber -> sip.h.x-lk-callednumber
        
        The worker reads these from sip_participant.attributes.
        
        Reference: https://www.twilio.com/docs/voice/twiml/sip
        """
        from urllib.parse import quote
        
        # URL encode values to handle special characters (like + in phone numbers)
        params = [
            f"{LIVEKIT_SIP_HEADER_TENANT_ID}={quote(tenant_id, safe='')}",
            f"{LIVEKIT_SIP_HEADER_CALL_ID}={quote(call_id, safe='')}",
            f"{LIVEKIT_SIP_HEADER_CALLED_NUMBER}={quote(called_number, safe='')}",
        ]
        
        # Join with & and prepend with ?
        query_string = "?" + "&".join(params)
        
        logger.info(f"[SIP HEADERS] Built query params: {query_string}")
        return query_string

    def _build_sip_uri_with_headers(self, tenant_id: str, call_id: str, called_number: str) -> str:
        """
        Build complete SIP URI with headers for inbound calls.
        
        TWILIO OFFICIAL FORMAT: Custom SIP headers as query parameters.
        sip:agent@domain.sip.livekit.cloud?X-LK-TenantId=abc&X-LK-CallId=123&X-LK-CalledNumber=%2B1234567890
        
        This ensures:
        - Twilio forwards headers to LiveKit via SIP INVITE
        - LiveKit maps headers to participant attributes (sip.h.x-lk-*)
        - Worker can extract tenant_id, call_id, and called_number reliably
        
        Reference: https://www.twilio.com/docs/voice/twiml/sip
        """
        domain = self._get_livekit_sip_domain()
        query_params = self._build_sip_query_params(tenant_id, call_id, called_number)
        
        # Use "agent" as the user portion of the SIP URI
        # Format: sip:agent@domain?params
        sip_uri = f"sip:agent@{domain}{query_params}"
        
        logger.info(f"[SIP URI] Built complete URI: {sip_uri}")
        return sip_uri

    def _get_livekit_sip_uri_with_room(self, room_name: str) -> str:
        """
        Generate SIP URI with room name (for outbound calls where we pre-create rooms).
        
        Format: sip:room_name@domain
        """
        if not room_name:
            raise ValueError("room_name is required")
        
        domain = self._get_livekit_sip_domain()
        sip_uri = f"sip:{room_name}@{domain}"
        
        logger.info(f"[SIP URI] Generated room-based SIP URI: {sip_uri}")
        return sip_uri

    async def _get_tenant_config(self, tenant_id: str) -> Dict[str, Any]:
        """Get tenant configuration from PostgreSQL."""
        try:
            config = await store.get_agent_settings(tenant_id)
            return config or {}
        except Exception as e:
            logger.warning(f"Failed to get tenant config from PostgreSQL: {e}")
            return {"business_name": "our company", "service_type": "Home Services"}

    def _get_system_prompt(
        self, tenant_config: Dict[str, Any], service_type: str, agent_config: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate system prompt for the agent."""
        business_name = tenant_config.get("business_name", "our company")

        if agent_config:
            agent_name = agent_config.get("name", "Agent")
            greeting = agent_config.get("greeting_message", "")
            language = agent_config.get("language", "en-US")

            return f"""You are {agent_name}, a professional appointment booking assistant for {business_name}, specializing in {service_type} services.

Your greeting: "{greeting}"

Your role is to help customers schedule appointments by collecting:
- Customer name
- Phone number
- Email address
- Service address
- Preferred date and time
- Service details and requirements

Be friendly, professional, and efficient. Confirm all details before booking the appointment.

Current service type: {service_type}
Business: {business_name}
Language: {language}"""
        else:
            return f"""You are a professional appointment booking assistant for {business_name}, specializing in {service_type} services.

Your role is to help customers schedule appointments.

Current service type: {service_type}
Business: {business_name}"""

    async def _create_browser_test_room(self, room_name: str) -> bool:
        """Create a LiveKit room for browser testing."""
        try:
            await self.livekit_api.room.create_room(
                api.CreateRoomRequest(name=room_name, empty_timeout=300, max_participants=10)
            )
            logger.info(f"[ROOM] Created room for browser testing: {room_name}")
            return True
        except Exception as e:
            logger.error(f"[ROOM] Error creating room: {e}")
            return False

    async def _delete_room(self, room_name: str) -> bool:
        """Delete a LiveKit room."""
        try:
            await self.livekit_api.room.delete_room(api.DeleteRoomRequest(room=room_name))
            logger.info(f"[ROOM] Deleted room: {room_name}")
            return True
        except Exception as e:
            logger.error(f"[ROOM] Error deleting room: {e}")
            return False

    def _generate_room_token(self, room_name: str, identity: str) -> str:
        """Generate a LiveKit room token."""
        try:
            token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
            token.with_identity(identity)
            token.with_name("Voice Agent Client")
            token.with_grants(
                api.VideoGrants(room_join=True, room=room_name, can_publish=True, can_subscribe=True, can_publish_data=True)
            )
            # Explicit agent dispatch is required because the worker sets agent_name.
            # Dispatch the "voice-agent" when the participant connects (per LiveKit docs).
            token.with_room_config(
                api.RoomConfiguration(
                    # Preserve previous room settings from create_room
                    empty_timeout=300,
                    max_participants=10,
                    agents=[
                        api.RoomAgentDispatch(
                            agent_name="voice-agent",
                            metadata=json.dumps({"room": room_name}),
                        )
                    ],
                )
            )
            return token.to_jwt()
        except Exception as e:
            logger.error(f"Error generating token: {e}")
            raise

    def _format_livekit_url(self) -> str:
        """Format LiveKit URL for WebSocket connection."""
        livekit_url = LIVEKIT_URL
        
        if not livekit_url or livekit_url.strip() == "":
            raise ValueError("LIVEKIT_URL is not configured")

        livekit_url = livekit_url.strip().rstrip("/")

        if not livekit_url.startswith(("ws://", "wss://")):
            if livekit_url.startswith("https://"):
                livekit_url = livekit_url.replace("https://", "wss://", 1)
            elif livekit_url.startswith("http://"):
                livekit_url = livekit_url.replace("http://", "ws://", 1)
            else:
                livekit_url = f"wss://{livekit_url}"

        return livekit_url

    async def _initiate_twilio_call(self, tenant_id: str, phone_number: str, room_name: str) -> str:
        """Initiate an outbound Twilio phone call."""
        try:
            twilio_integration = await store.get_twilio_integration(tenant_id)
            if twilio_integration and "auth_token" in twilio_integration:
                try:
                    twilio_integration["auth_token"] = encryption_service.decrypt(twilio_integration["auth_token"])
                except Exception as e:
                    logger.error(f"Failed to decrypt auth_token: {e}")
                    raise

            if not twilio_integration:
                raise ValueError(f"No Twilio integration found for tenant {tenant_id}")

            webhook_url = f"{TWILIO_WEBHOOK_BASE_URL}/api/v1/voice-agent/twilio/webhook"

            def _initiate_call_sync():
                twilio_client = Client(twilio_integration["account_sid"], twilio_integration["auth_token"])
                call = twilio_client.calls.create(
                    from_=twilio_integration["phone_number"],
                    to=phone_number,
                    url=webhook_url,
                    status_callback=f"{TWILIO_WEBHOOK_BASE_URL}/api/v1/voice-agent/twilio/status",
                    status_callback_event=["initiated", "ringing", "answered", "completed"],
                )
                logger.info(f"Initiated Twilio call: {call.sid}")
                return call.sid

            return await asyncio.to_thread(_initiate_call_sync)

        except Exception as e:
            logger.error(f"Error initiating Twilio call: {e}")
            raise

    async def _hangup_twilio_call(self, tenant_id: str, call_sid: str):
        """Hang up a Twilio call."""
        try:
            twilio_integration = await store.get_twilio_integration(tenant_id)
            if twilio_integration and "auth_token" in twilio_integration:
                try:
                    twilio_integration["auth_token"] = encryption_service.decrypt(twilio_integration["auth_token"])
                except Exception as e:
                    logger.error(f"Failed to decrypt auth_token: {e}")
                    return
                    
            if twilio_integration:
                def _hangup_call_sync():
                    twilio_client = Client(twilio_integration["account_sid"], twilio_integration["auth_token"])
                    twilio_client.calls(call_sid).update(status="completed")
                    logger.info(f"Hung up Twilio call: {call_sid}")

                await asyncio.to_thread(_hangup_call_sync)
        except Exception as e:
            logger.error(f"Error hanging up call: {e}")


# Global instance
unified_voice_agent_service = UnifiedVoiceAgentService()

