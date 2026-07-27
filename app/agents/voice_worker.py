"""
LiveKit Voice Agent Worker
===========================
Runs Gemini Live (`gemini-2.5-flash-native-audio-preview-12-2025`) end-to-end:
the model handles VAD, turn-taking, STT, LLM reasoning and TTS in a single
realtime session. This worker connects to LiveKit and handles voice agent
sessions for inbound telephony.

NOTE: We stay on the 2.5 native-audio model rather than 3.1-flash-live
because 3.1 disables every programmatic "agent speaks first" mechanism
(`proactivity`, `generate_reply()`, `update_instructions()` are all
ignored/unsupported by the LiveKit google plugin), which breaks the
opening greeting. See https://github.com/livekit/agents/issues/5260
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from livekit import agents
from livekit.agents import Agent, AgentSession, function_tool
from livekit.plugins.google.realtime import RealtimeModel
from google.genai.types import (
    AutomaticActivityDetection,
    EndSensitivity,
    Modality,
    RealtimeInputConfig,
    StartSensitivity,
)

from app.core.async_redis import async_redis_client
from app.core.config import (
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
)
from app.core.prompts import build_agent_instructions

# Gemini Live voices supported by `gemini-live-2.5-flash-native-audio`.
# https://ai.google.dev/gemini-api/docs/live#voices
_GEMINI_LIVE_VOICES = frozenset({
    "Achernar", "Achird", "Algenib", "Algieba", "Alnilam", "Aoede", "Autonoe",
    "Callirrhoe", "Charon", "Despina", "Enceladus", "Erinome", "Fenrir",
    "Gacrux", "Iapetus", "Kore", "Laomedeia", "Leda", "Orus", "Pulcherrima",
    "Puck", "Rasalgethi", "Sadachbia", "Sadaltager", "Schedar", "Sulafat",
    "Umbriel", "Vindemiatrix", "Zephyr", "Zubenelgenubi",
})
GEMINI_LIVE_DEFAULT_VOICE = "Puck"
# Gemini-API-compatible Live model (uses GOOGLE_API_KEY). The plain
# `gemini-live-2.5-flash-native-audio` alias is a VertexAI-only model and
# requires a GCP project + `vertexai=True`. The `-preview-12-2025` suffix
# is the corresponding Gemini API endpoint.
GEMINI_LIVE_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

# Server-side automatic activity detection — replaces client-side VAD +
# AgentSession endpointing. The defaults Live ships with are tuned for
# desktop microphones (long silence windows, conservative); for telephony
# we want tighter turn closure for snappier responses.
# https://ai.google.dev/gemini-api/docs/live#voice-activity-detection
_GEMINI_LIVE_REALTIME_INPUT_CONFIG = RealtimeInputConfig(
    automatic_activity_detection=AutomaticActivityDetection(
        start_of_speech_sensitivity=StartSensitivity.START_SENSITIVITY_HIGH,
        end_of_speech_sensitivity=EndSensitivity.END_SENSITIVITY_HIGH,
        silence_duration_ms=100,
        prefix_padding_ms=200,
    ),
)

# Hard upper bound on any single call. Even if the LLM ignores the prompt's
# "call end_call after the closing line" instruction, this watchdog guarantees
# the session is torn down so Twilio billing stops and worker capacity is
# released. Tune per traffic; 5 minutes covers a full appointment booking with
# headroom for slow speech / corrections.
MAX_CALL_DURATION_SECONDS = int(os.environ.get("VOICE_AGENT_MAX_CALL_DURATION", "300"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VoiceAgentWorker")


# =========================================================
# AGENT
# =========================================================
def _parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO 8601 datetime string (incl. 'Z') into a tz-aware UTC datetime.

    Raises ValueError with a clear message so the calling tool can return that
    message to the LLM, which then knows to ask the user for a clearer time.
    """
    s = (value or "").strip()
    if not s:
        raise ValueError("empty datetime string")
    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValueError(f"invalid ISO 8601 datetime: {value!r}") from exc
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class VoiceAgent(Agent):
    """Voice agent with the tools the prompt actually calls.

    Tools exposed to the LLM (kept minimal — every extra @function_tool
    inflates the per-turn schema the model evaluates):
      * ``book_appointment`` — appointment-booking agents; persists + emails.
      * ``capture_lead``     — lead-capture agents (e.g. Lisa); emails team.
      * ``end_call``         — terminate the session after the closing line.

    Internal helpers (not @function_tool):
      * ``send_appointment_confirmation_email`` — direct email primitive,
        used by tests; not visible to the LLM.
    """

    def __init__(
        self,
        instructions: str,
        tenant_id: Optional[str],
        call_id: Optional[str] = None,
        agent_name: str = "",
        service_type: str = "",
        job_ctx: Optional[agents.JobContext] = None,
    ):
        super().__init__(instructions=instructions)
        self.tenant_id = tenant_id
        self.call_id = call_id
        # Agent context — auto-injected into capture_lead notifications so
        # the LLM never has to know or pass its own identity. The team's
        # inbox shows which agent took the call and which vertical it was.
        self.agent_name = (agent_name or "").strip()
        self.service_type = (service_type or "").strip()
        # JobContext is the official handle for ending a SIP call —
        # `job_ctx.api.room.delete_room(...)` disconnects the SIP participant
        # (Twilio's leg) and drops the caller's phone. session.aclose() does
        # NOT reliably terminate SIP, which is the production symptom we saw
        # ("speech not done in time after interruption", "entrypoint did not
        # exit in time"). Ref: https://docs.livekit.io/recipes/sip_lifecycle/
        self._job_ctx = job_ctx
        self._booking_completed = False  # idempotency guard for book_appointment
        self._lead_captured = False      # idempotency guard for capture_lead

    # ------------------------------------------------------------------
    # Tool: book the appointment + send emails
    # ------------------------------------------------------------------
    @function_tool
    async def book_appointment(
        self,
        customer_name: str,
        customer_phone: str,
        customer_email: str,
        service_type: str,
        service_address: str,
        appointment_datetime: str,
        service_details: str = "",
    ) -> str:
        """Persist the appointment and send confirmation emails.

        Call this EXACTLY ONCE, after the caller has confirmed every field.
        `appointment_datetime` must be ISO 8601 (e.g. "2025-12-25T14:30:00Z").
        Returns a short status string for the LLM to react to: on success it
        instructs the LLM to deliver the closing line and call end_call; on
        failure it instructs the LLM to apologise and offer to retry.
        """
        if self._booking_completed:
            return (
                "Already booked. Deliver the closing line and call end_call."
            )
        if not self.tenant_id:
            logger.error("[BOOK] tenant_id missing on VoiceAgent; cannot book")
            return (
                "Booking failed: missing tenant context. Apologise to the "
                "caller and call end_call."
            )
        try:
            appointment_dt = _parse_iso_datetime(appointment_datetime)
        except ValueError as exc:
            logger.warning("[BOOK] Bad datetime from LLM: %s", exc)
            return (
                f"Booking failed: {exc}. Ask the caller to restate the date "
                "and time clearly."
            )

        from app.api.v1.services.appointment import appointment_service

        clean_email = (customer_email or "").strip() or None
        clean_name = customer_name.strip()
        clean_service = service_type.strip()
        clean_address = service_address.strip()
        clean_details = (service_details or "").strip() or None

        try:
            appointment = await appointment_service.create_appointment(
                tenant_id=self.tenant_id,
                customer_name=clean_name,
                customer_phone=customer_phone.strip(),
                customer_email=clean_email,
                service_type=clean_service,
                service_address=clean_address,
                appointment_datetime=appointment_dt,
                service_details=clean_details,
                call_id=self.call_id,
                # Own the email path so we can tell the LLM whether it
                # actually went out, instead of pretending success.
                send_email=False,
            )
        except Exception:
            logger.exception("[BOOK] create_appointment crashed")
            return (
                "Booking failed due to a system error. Apologise to the "
                "caller and call end_call."
            )

        if not appointment:
            # create_appointment returns None on validation/persistence errors;
            # it has already logged the cause.
            return (
                "Booking failed (the time may be unavailable). Offer the "
                "caller a different time or call end_call."
            )

        # Send the customer confirmation ourselves so we can read the
        # boolean status. `send_appointment_confirmation` catches its own
        # SMTP errors and returns False, logging the cause; we use that
        # signal to tell the LLM the truth instead of empty-promising an
        # email that never went out (common SMTP causes: MAIL_FROM domain
        # does not match the authenticated SMTP user — handled by the
        # Gmail-From defense in EmailService — or auth failure, or the
        # SMTP server unreachable). The booking still succeeds in the DB
        # regardless.
        customer_email_sent = False
        if clean_email:
            try:
                customer_email_sent = bool(
                    await appointment_service.email_service.send_appointment_confirmation(
                        clean_email,
                        clean_name,
                        appointment_dt,
                        clean_service,
                        clean_address,
                        appointment_id=appointment.get("id"),
                        service_details=clean_details,
                    )
                )
            except Exception:
                logger.exception("[BOOK] customer confirmation email send raised")

        # Team-notification email — uses the SAME generic `send_lead_notification`
        # primitive that capture_lead uses, so the team inbox gets one
        # consistent format for every captured "thing" across every agent
        # vertical (appointment bookings AND lead captures). Non-fatal: we
        # never let a flaky team email sink the booking.
        team_email_sent = False
        try:
            team_email_sent = await self._send_team_lead_notification_for_appointment(
                customer_name=clean_name,
                customer_phone=customer_phone.strip(),
                customer_email=clean_email or "",
                appointment_datetime=appointment_dt,
                service_type=clean_service,
                service_address=clean_address,
                service_details=clean_details or "",
                appointment_id=appointment.get("id") or "",
            )
        except Exception as exc:
            logger.warning("[BOOK] Team notification failed (non-fatal): %s", exc)

        self._booking_completed = True
        logger.info(
            "[BOOK] Created appointment id=%s tenant=%s call=%s "
            "customer_email_sent=%s team_email_sent=%s",
            appointment.get("id"), self.tenant_id, self.call_id,
            customer_email_sent, team_email_sent,
        )
        if customer_email_sent:
            return (
                "Appointment booked AND confirmation email sent. Speak a "
                "short closing line out loud that confirms the email is on "
                "its way, then call end_call."
            )
        return (
            "Appointment booked, BUT the confirmation email did NOT go "
            "out (SMTP failure - check backend logs). Speak a short closing "
            "line out loud that apologises briefly and says a teammate will "
            "follow up by phone, then call end_call. Do NOT promise an email."
        )

    # ------------------------------------------------------------------
    # Tool: generic lead capture — usable by any agent vertical
    # ------------------------------------------------------------------
    @function_tool
    async def capture_lead(
        self,
        customer_name: str,
        customer_phone: str,
        customer_email: str,
        summary: str,
        details: str = "",
    ) -> str:
        """Capture caller info and email the tenant's team for follow-up.

        Generic primitive — works for ANY agent vertical (scholarly help,
        real estate, healthcare scheduling, etc.). The recipient is the
        tenant's configured `owner_email`. The agent's identity and
        vertical are auto-injected from the agent's configuration, so the
        LLM never needs to pass them.

        Call EXACTLY ONCE per call, AFTER all the fields your prompt's
        workflow tells you to gather have been collected and confirmed by
        the caller, and BEFORE end_call.

        Arguments:
            customer_name:  The caller's full name.
            customer_phone: The caller's phone number (any common format).
                            Pass the confirmed digits — the email will
                            tel:-link them automatically.
            customer_email: The caller's email. Pass "" if none was given.
            summary:        ONE line, the headline of the lead. Becomes the
                            email subject. Examples:
                              "Exam help — proctored, due 12/25 14:30 EST"
                              "Buyer interested in 2BR condo, Westwood"
                              "Annual check-up booking, prefers mornings"
            details:        Multi-line free-form body — the structured
                            fields you captured, one per line as
                            "Field: Value". Newlines are preserved in the
                            email. Pass "" if there's nothing beyond the
                            summary. Examples:
                              Service: Exam help
                              Proctored: Yes
                              Due: 2025-12-25 14:30 EST
                              Subject: Statistics

        Returns a status string the LLM uses to choose its spoken closing line:
          * success -> closing should confirm the team will follow up.
          * failure -> closing should say a teammate will call back; do
                       NOT promise a specific follow-up channel.
        """
        if self._lead_captured:
            return "Lead already captured. Speak a brief closing line out loud, then call end_call."
        if not self.tenant_id:
            logger.error("[LEAD] tenant_id missing on VoiceAgent; cannot capture lead")
            return (
                "Captured the details locally but couldn't notify the team "
                "(missing tenant context). Apologise briefly and call end_call."
            )

        from app.services.email.service import EmailService
        from app.services.store import store

        # Resolve where the team notification goes — the tenant's owner
        # email. Same field that drives appointment owner-notifications.
        try:
            tenant = await store.get_tenant(self.tenant_id)
        except Exception:
            logger.exception("[LEAD] failed to load tenant for lead notification")
            tenant = None
        team_email = ((tenant or {}).get("owner_email") or "").strip()
        tenant_name = ((tenant or {}).get("name") or "").strip()

        clean_name = customer_name.strip() or "Unknown caller"
        clean_phone = customer_phone.strip()
        clean_email = (customer_email or "").strip()
        clean_summary = summary.strip() or "(no summary provided)"
        clean_details = (details or "").strip()

        lead_data = {
            "tenant_id": self.tenant_id,
            "tenant_name": tenant_name,
            "call_id": self.call_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "agent_name": self.agent_name,
            "service_type": self.service_type,
            "customer_name": clean_name,
            "customer_phone": clean_phone,
            "customer_email": clean_email,
            "summary": clean_summary,
            "details": clean_details,
        }

        if not team_email:
            # No team inbox configured — log the full payload so the lead
            # is at least recoverable from worker logs.
            logger.error(
                "[LEAD] tenant=%s has no owner_email; cannot notify team. lead=%s",
                self.tenant_id, lead_data,
            )
            self._lead_captured = True
            return (
                "Captured the lead but the team inbox isn't configured. "
                "Apologise briefly and call end_call."
            )

        email_sent = False
        try:
            email_sent = await EmailService().send_lead_notification(team_email, lead_data)
        except Exception:
            logger.exception("[LEAD] team notification email crashed")

        self._lead_captured = True
        logger.info(
            "[LEAD] captured tenant=%s call=%s team=%s email_sent=%s agent=%s service=%s phone=%s",
            self.tenant_id, self.call_id, team_email, email_sent,
            self.agent_name or "<unknown>", self.service_type or "<unknown>", clean_phone,
        )

        if email_sent:
            return (
                "Lead captured and the team has been notified by email. "
                "Speak a short closing line out loud that confirms a teammate "
                "will follow up shortly, then call end_call."
            )
        return (
            "Lead captured but the team-notification email did NOT go out "
            "(check backend logs). Speak a short closing line out loud saying "
            "a teammate will call the caller back, then call end_call. Do "
            "NOT promise a specific channel like text or email."
        )

    # ------------------------------------------------------------------
    # Email primitive — used internally + directly by tests/admin code
    # ------------------------------------------------------------------
    async def send_appointment_confirmation_email(
        self,
        customer_email: str,
        customer_name: str,
        appointment_time: str,
        service_type: str,
        service_address: str,
    ) -> str:
        """Send the customer confirmation + the tenant-owner notification.

        Standalone primitive (no DB insert) — useful for retry/admin flows
        and exercised by `tests/test_voice_worker_email.py`. Returns the
        canonical success string for callers that need to detect success.
        """
        from app.services.email.service import EmailService
        from app.services.store import store

        appointment_dt = _parse_iso_datetime(appointment_time)

        owner_email = ""
        if self.tenant_id:
            tenant = await store.get_tenant(self.tenant_id)
            owner_email = (tenant or {}).get("owner_email") or ""

        email_service = EmailService()
        await email_service.send_appointment_confirmation(
            customer_email,
            customer_name,
            appointment_dt,
            service_type,
            service_address,
        )
        if owner_email:
            await email_service.send_appointment_owner_notification(
                owner_email,
                customer_name,
                customer_email,
                appointment_dt,
                service_type,
                service_address,
            )
        return "Appointment confirmation email sent successfully."

    async def _send_team_lead_notification_for_appointment(
        self,
        customer_name: str,
        customer_phone: str,
        customer_email: str,
        appointment_datetime: datetime,
        service_type: str,
        service_address: str,
        service_details: str = "",
        appointment_id: str = "",
    ) -> bool:
        """Send the tenant's team a lead-shaped notification for a new booking.

        Uses the same generic `send_lead_notification` primitive that
        capture_lead uses, so the team inbox is consistent across every
        agent vertical. Returns True on successful SMTP delivery, False
        otherwise; the caller logs the bool for visibility.
        """
        from app.services.email.service import EmailService
        from app.services.store import store

        if not self.tenant_id:
            return False
        tenant = await store.get_tenant(self.tenant_id)
        if not tenant:
            return False
        team_email = (tenant.get("owner_email") or "").strip()
        if not team_email:
            return False

        # Render the appointment as a lead payload — same shape Lisa uses,
        # so the team email format is identical across agents.
        when_pretty = appointment_datetime.strftime("%B %d, %Y at %I:%M %p %Z").strip()
        summary = f"Appointment booked - {service_type or 'service'}"
        if when_pretty:
            summary += f", {when_pretty}"

        details_lines = []
        if service_type:
            details_lines.append(f"Service: {service_type}")
        if service_address:
            details_lines.append(f"Address: {service_address}")
        if when_pretty:
            details_lines.append(f"When: {when_pretty}")
        if service_details:
            details_lines.append(f"Notes: {service_details}")
        if appointment_id:
            details_lines.append(f"Appointment ID: {appointment_id}")

        lead_data = {
            "tenant_id": self.tenant_id,
            "tenant_name": (tenant.get("name") or "").strip(),
            "call_id": self.call_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "agent_name": self.agent_name,
            "service_type": self.service_type,
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "customer_email": customer_email,
            "summary": summary,
            "details": "\n".join(details_lines),
        }
        return await EmailService().send_lead_notification(team_email, lead_data)

    # ------------------------------------------------------------------
    # Tools: end the call cleanly — official LiveKit SIP recipe
    # ------------------------------------------------------------------
    # Why delete_room and not session.aclose():
    #   "Deleting the room disconnects all remote users, including SIP
    #    callers." — https://docs.livekit.io/agents/prebuilt/tools/end-call-tool/
    # session.aclose() is the agent's view of teardown and does not
    # reliably propagate SIP BYE to Twilio. The earlier production error
    # ("speech not done in time after interruption, cancelling the speech
    # arbitrarily") was the symptom of aclose() racing in-flight speech.
    async def _finish_call(self) -> str:
        logger.info(
            "[CALL] finishing call (tenant=%s call=%s)",
            self.tenant_id, self.call_id,
        )

        # Gemini Live closes the call the natural way: the model speaks the
        # goodbye line as part of the same turn that invokes end_call (per
        # the prompt's END CALL section). All this method does is wait for
        # that in-flight audio to finish playing — then delete the room.
        #
        # We do NOT call generate_reply here, and we do NOT interrupt the
        # current speech: doing either fights the Live model and produces
        # the silent-hangup symptom (model emits goodbye -> we cancel it ->
        # delete_room before audio drains -> caller hears nothing).
        try:
            current = self.session.current_speech
            if current is not None:
                try:
                    await asyncio.wait_for(
                        current.wait_for_playout(), timeout=15.0,
                    )
                    logger.info("[CALL] closing-line audio drained")
                except asyncio.TimeoutError:
                    logger.warning(
                        "[CALL] timed out (15s) waiting for closing-line playout; hanging up anyway",
                    )
            else:
                logger.warning("[CALL] no current Gemini speech to drain before hangup")
        except Exception as exc:
            logger.warning("[CALL] error draining closing-line audio: %s", exc)

        # Step 2b: suppress a repeated closing line.
        #
        # `proactivity=True` (which we need so the model OPENS the call with
        # the greeting) also makes the model spontaneously start a SECOND
        # turn — another goodbye / "is there anything else?" — in the gap
        # between the intended goodbye draining above and the room being
        # deleted below. The caller hears the closing line twice.
        #
        # Once the intended goodbye has drained, interrupt the session to
        # cancel any proactive follow-up turn before we hang up. This runs
        # AFTER the drain, so it never clips the real goodbye — it only
        # kills the unwanted repeat.
        try:
            fut = self.session.interrupt()
            # interrupt() returns a Future that resolves once the in-flight
            # speech is actually cancelled; await it (briefly) so the proactive
            # turn is stopped before we delete the room rather than racing it.
            try:
                await asyncio.wait_for(asyncio.ensure_future(fut), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            logger.info("[CALL] interrupted any proactive follow-up turn before hangup")
        except Exception as exc:
            logger.warning("[CALL] error interrupting proactive follow-up: %s", exc)

        # Step 3: hard hangup via room deletion — the only reliable way to
        # terminate the SIP participant. Falls back to aclose() only if the
        # job context isn't available.
        if self._job_ctx is not None:
            try:
                from livekit import api as lk_api

                await self._job_ctx.api.room.delete_room(
                    lk_api.DeleteRoomRequest(room=self._job_ctx.room.name)
                )
                logger.info(
                    "[CALL] room deleted; SIP participant disconnected (tenant=%s call=%s)",
                    self.tenant_id, self.call_id,
                )
                return "Call ended."
            except Exception as exc:
                logger.warning(
                    "[CALL] delete_room failed (%s); falling back to session.aclose()",
                    exc,
                )

        try:
            await self.session.aclose()
        except Exception as exc:
            logger.warning("[CALL] aclose() raised during finish: %s", exc)
        return "Call ended."

    @function_tool
    async def end_call(self) -> str:
        """End the call after Gemini has spoken its goodbye out loud.

        Call exactly ONCE per call: after book_appointment succeeds, when
        the caller asks to hang up, or after retry discipline is exhausted.
        The same Gemini turn must include the spoken goodbye before this tool
        call; this tool only waits for the current speech to finish playing
        and then hangs up.
        """
        return await self._finish_call()


# =========================================================
# ENTRYPOINT
# =========================================================
async def entrypoint(ctx: agents.JobContext):
    logger.info("=" * 80)
    logger.info("[WORKER ENTRYPOINT] Voice agent starting")
    logger.info(f"[WORKER ENTRYPOINT] Room: {ctx.room.name}")
    logger.info("=" * 80)

    # 1️⃣ Connect first
    await ctx.connect()
    await asyncio.sleep(0.5)

    # 2️⃣ Determine connection type: SIP (phone) or WebRTC (browser)
    sip_participant = None
    for p in ctx.room.remote_participants.values():
        if p.attributes:
            # Check if this is a SIP participant (has SIP attributes)
            attrs = p.attributes
            if attrs.get("lk_callid") or attrs.get("sip.h.x-lk-callid") or attrs.get("sip.twilio.callSid"):
                sip_participant = p
                break

    config = None
    tenant_id = None
    call_id = None

    if sip_participant:
        # ========================================
        # PHONE CALL MODE (SIP participant)
        # ========================================
        logger.info("[WORKER ENTRYPOINT] Detected SIP participant (phone call)")
        attrs = sip_participant.attributes
        original_call_sid = attrs.get("lk_callid") or attrs.get("sip.h.x-lk-callid") or attrs.get("sip.twilio.callSid")

        if not original_call_sid:
            raise RuntimeError("Missing CallSid in SIP attributes")

        logger.info(f"[WORKER ENTRYPOINT] CallSid: {original_call_sid}")

        # Load config from Redis by CallSid
        config = await async_redis_client.get(f"call_config:{original_call_sid}")
        if not config:
            raise RuntimeError(f"No config found for CallSid {original_call_sid}")

        config = json.loads(config)
        tenant_id = config.get("tenant_id")
        call_id = config.get("call_id")
        logger.info(f"[WORKER ENTRYPOINT] Loaded config for tenant: {tenant_id}, call_id: {call_id}")

    else:
        # ========================================
        # BROWSER TEST MODE (WebRTC participant)
        # ========================================
        logger.info("[WORKER ENTRYPOINT] Detected WebRTC participant (browser test)")
        
        # Browser test rooms: config is stored by room name
        room_name = ctx.room.name
        config_key = f"room_config:{room_name}"
        logger.info(f"[WORKER ENTRYPOINT] Looking up config for room: {room_name}")
        
        config = await async_redis_client.get(config_key)
        if not config:
            raise RuntimeError(f"No config found for browser test room: {room_name}")
        
        config = json.loads(config)
        tenant_id = config.get("tenant_id")
        call_id = config.get("call_id") or config.get("session_id")
        logger.info(f"[WORKER ENTRYPOINT] Loaded browser test config for tenant: {tenant_id}, call_id: {call_id}")

    # 4️⃣ Build instructions. Append the configured greeting as an explicit
    # opening-line directive so the Live model produces it as its very first
    # turn — no per-turn `instructions=...` round-trip needed. Falling back
    # to a generic greeting if the agent record didn't configure one.
    service_type = config.get("service_type", "Home Services")
    agent_data = config.get("agent_data") or {}
    agent_name = agent_data.get("name", "Assistant")
    greeting_message = agent_data.get("greeting_message") or ""
    instructions = build_agent_instructions(
        service_type=service_type,
        agent_name=agent_name,
        greeting_message=greeting_message,
        system_prompt=agent_data.get("system_prompt"),
    )

    # 5️⃣ Create agent. Pass the JobContext through so the agent can call
    # `ctx.api.room.delete_room(...)` for the official SIP hangup.
    # agent_name + service_type are forwarded so the generic capture_lead
    # tool can auto-inject them into the team notification email without
    # the LLM having to know its own identity.
    agent = VoiceAgent(
        instructions=instructions,
        tenant_id=tenant_id,
        call_id=call_id,
        agent_name=agent_name,
        service_type=service_type,
        job_ctx=ctx,
    )

# =====================================================
    # ✅ AGENT SESSION — Gemini Live (multimodal realtime)
    # =====================================================
    # The Gemini Live model does STT + LLM + TTS + turn detection in a
    # single bidirectional stream. All of those layers happen server-side
    # at Google, so we do NOT pass `vad=`, `min_endpointing_delay=`,
    # `max_endpointing_delay=`, `allow_interruptions=`, or any other
    # client-side endpointing knobs — they'd just add a redundant
    # processing hop before the model sees the audio.
    #
    # Latency tuning lives on the RealtimeModel via `realtime_input_config`
    # and `proactivity` — both of which push tightening to the model
    # boundary where it actually matters.
    # https://ai.google.dev/gemini-api/docs/live
    chosen_voice = (config.get("voice_id") or "").strip()
    live_voice = chosen_voice if chosen_voice in _GEMINI_LIVE_VOICES else GEMINI_LIVE_DEFAULT_VOICE
    logger.info(
        "[REALTIME] Using Gemini Live model=%s voice=%s (requested=%r)",
        GEMINI_LIVE_MODEL, live_voice, chosen_voice or None,
    )

    session = AgentSession(
        llm=RealtimeModel(
            model=GEMINI_LIVE_MODEL,
            voice=live_voice,
            language="en-US",
            temperature=0.4,
            # Audio-only output — no parallel text generation overhead.
            modalities=[Modality.AUDIO],
            # Skip server-side transcription we never read.
            input_audio_transcription=None,
            output_audio_transcription=None,
            realtime_input_config=_GEMINI_LIVE_REALTIME_INPUT_CONFIG,
            # Proactive audio: lets the model open the conversation (the
            # greeting) and supports generate_reply()-driven first turns.
            # Supported on the 2.5 native-audio model.
            proactivity=True,
        ),
    )

    await session.start(room=ctx.room, agent=agent)
    logger.info("[WORKER ENTRYPOINT] ✓ Agent session started")

    # 7️⃣ Trigger the Live model's first turn. The OPENING LINE directive
    # appended to the prompt above tells the model exactly what to say;
    # we just need to nudge it to start (Gemini Live doesn't auto-emit
    # without a generate_reply call).
    try:
        opening_handle = session.generate_reply()
        await asyncio.wait_for(opening_handle.wait_for_playout(), timeout=15.0)
        logger.info("[WORKER ENTRYPOINT] Greeting played: %r", greeting_message)
    except Exception as exc:
        logger.warning("[WORKER ENTRYPOINT] greeting playout error: %s", exc)

    closed_event = asyncio.Event()

    @session.on("close")
    def on_close(_):
        async def cleanup():
            try:
                # Clean up both tenant_config and room_config
                await async_redis_client.delete(f"tenant_config:{tenant_id}:{call_id}")
                await async_redis_client.delete(f"room_config:{ctx.room.name}")
                logger.info("[CLEANUP] Deleted per-call configs")
            except Exception as e:
                logger.warning(f"[CLEANUP] Failed: {e}")
            closed_event.set()

        asyncio.create_task(cleanup())

    # Hard cap on call duration. Even if the LLM ignores the prompt and never
    # calls end_call, this guarantees the session is torn down so Twilio billing
    # stops, the agent slot is released, and we have a predictable upper bound
    # on call cost. The watchdog is cancelled when the session closes normally.
    async def _call_duration_watchdog() -> None:
        try:
            await asyncio.sleep(MAX_CALL_DURATION_SECONDS)
        except asyncio.CancelledError:
            return
        if closed_event.is_set():
            return
        logger.warning(
            "[CALL] Max duration %ss reached for tenant=%s call=%s — force-closing",
            MAX_CALL_DURATION_SECONDS, tenant_id, call_id,
        )
        try:
            await session.aclose()
        except Exception as exc:
            logger.warning("[CALL] aclose() during watchdog raised: %s", exc)

    watchdog_task = asyncio.create_task(_call_duration_watchdog())
    try:
        await closed_event.wait()
    finally:
        watchdog_task.cancel()
    logger.info(f"[WORKER ENTRYPOINT] Call completed for tenant {tenant_id}")


# =========================================================
# WORKER BOOTSTRAP
# =========================================================
if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()

    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
            ws_url=LIVEKIT_URL,
            agent_name="voice-agent",
        )
    )
