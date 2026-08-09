"""Twilio status callbacks: call completion, recordings, and inbound SMS.

The recording callback is where the consent gate is enforced for real.
`set_recording_url` refuses the write when `consent_captured` is false and
returns whether it happened, so a refusal is logged rather than passing silently
as a success.

Inbound SMS exists for one reason: STOP. Twilio suppresses its own delivery, but
our `contacts` table is what stops *us* composing the next message.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from apps.api.db import repository
from apps.api.db.session import session_scope
from apps.api.observability.logging import get_logger
from apps.api.security.redaction import mask_e164
from apps.api.telephony.sms import is_opt_out, send_sms
from apps.api.telephony.twiml import validate_twilio_request

log = get_logger(__name__)

router = APIRouter(prefix="/telephony", tags=["telephony"])

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response/>'

# Twilio's terminal call statuses. Anything else is an intermediate ping.
TERMINAL_STATUSES = frozenset({"completed", "busy", "no-answer", "canceled", "failed"})


@router.post("/status")
async def call_status(request: Request) -> Response:
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not await validate_twilio_request(request, form):
        return Response(status_code=403, content="invalid signature")

    call_sid = form.get("CallSid", "")
    status = form.get("CallStatus", "")
    if status not in TERMINAL_STATUSES:
        return Response(content=EMPTY_TWIML, media_type="application/xml")

    async with session_scope() as session:
        call = await repository.get_call_by_sid(session, call_sid)
        if call is None:
            log.warning("status_for_unknown_call", call_sid=call_sid, status=status)
            return Response(content=EMPTY_TWIML, media_type="application/xml")

        # A call that never reached the media plane has no outcome yet; the
        # gateway's own `finally` sets one for every call it did handle.
        missed = status in ("no-answer", "busy", "failed")
        if call.outcome is None:
            outcome = "abandoned" if status in ("no-answer", "canceled", "busy") else "failed"
            await repository.finish_call(session, call.id, outcome=outcome)
            log.info("status_finalised_call", call_sid=call_sid, status=status, outcome=outcome)
        client_id = call.client_id
        from_e164 = call.from_e164

    if missed and from_e164:
        # `send_sms` performs the suppression lookup before the provider call.
        # Keyed on the call SID: Twilio retries this callback on any non-2xx and
        # on its own timeouts, and one missed call is one text.
        await send_sms(
            to=from_e164,
            client_id=client_id,
            dedupe_key=f"missed_call:{call_sid}",
            body="We missed your call. Reply to this message and our team will call you back.",
        )

    return Response(content=EMPTY_TWIML, media_type="application/xml")


@router.post("/recording")
async def recording_status(request: Request) -> Response:
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not await validate_twilio_request(request, form):
        return Response(status_code=403, content="invalid signature")

    call_sid = form.get("CallSid", "")
    recording_status = form.get("RecordingStatus", "completed")
    if recording_status != "completed":
        # Do not expose an in-progress or absent URL as playable dashboard
        # state. Twilio sends the completed callback when media is available.
        return Response(content=EMPTY_TWIML, media_type="application/xml")
    url = form.get("RecordingUrl", "")
    if not url:
        return Response(content=EMPTY_TWIML, media_type="application/xml")

    async with session_scope() as session:
        call = await repository.get_call_by_sid(session, call_sid)
        if call is None:
            log.warning("recording_for_unknown_call", call_sid=call_sid)
            return Response(content=EMPTY_TWIML, media_type="application/xml")
        stored = await repository.set_recording_url(session, call.id, url)

    if not stored:
        # Not an error. It is the control working: no consent, no stored URL.
        log.info("recording_discarded_no_consent", call_sid=call_sid)
    return Response(content=EMPTY_TWIML, media_type="application/xml")


@router.post("/sms")
async def inbound_sms(request: Request) -> Response:
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not await validate_twilio_request(request, form):
        return Response(status_code=403, content="invalid signature")

    from_number = form.get("From", "")
    body = form.get("Body", "")
    to_number = form.get("To", "")

    if not is_opt_out(body):
        return Response(content=EMPTY_TWIML, media_type="application/xml")

    from apps.api.config.loader import ClientConfigNotFound, get_registry

    try:
        config = get_registry().resolve_by_number(to_number)
    except ClientConfigNotFound:
        log.warning("sms_opt_out_unrouted", to=mask_e164(to_number))
        return Response(content=EMPTY_TWIML, media_type="application/xml")

    async with session_scope() as session:
        await repository.mark_opted_out(
            session, client_id=config.client_id, phone_e164=from_number
        )
    log.info("sms_opt_out_recorded", client_id=config.client_id, from_e164=mask_e164(from_number))
    # Twilio sends its own STOP confirmation; adding ours would be a second
    # message to someone who just asked for none.
    return Response(content=EMPTY_TWIML, media_type="application/xml")
