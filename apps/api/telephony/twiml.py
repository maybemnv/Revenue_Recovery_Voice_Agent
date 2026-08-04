"""Twilio's inbound entry point, and the whisper leg for warm transfers.

The TwiML is generated as a string rather than through the Twilio helper library
because it is eleven lines of XML that has to be exactly right, and a string is
something a test can assert on character by character.

Signature validation is on by default. It can be disabled for local ngrok work
via `TWILIO_VALIDATE_SIGNATURES=false`, and the setting is logged loudly at
startup so it cannot quietly stay off in production.
"""

from __future__ import annotations

import hmac
import uuid
from base64 import b64encode
from hashlib import sha1
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

from fastapi import APIRouter, Request, Response

from apps.api.config.loader import ClientConfigNotFound, get_registry
from apps.api.db import repository
from apps.api.db.session import session_scope
from apps.api.observability.logging import get_logger
from apps.api.security.redaction import mask_e164
from apps.api.settings import get_settings

log = get_logger(__name__)

router = APIRouter(tags=["telephony"])

CONSENT_LINE = "This call may be recorded for quality and training."

REJECT_TWIML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<Response>"
    "<Say>Sorry, this number is not currently in service. Goodbye.</Say>"
    "<Hangup/>"
    "</Response>"
)


def compute_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Twilio's scheme: URL, then sorted key+value pairs, HMAC-SHA1, base64."""
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode("utf-8"), sha1).digest()
    return b64encode(digest).decode()


async def validate_twilio_request(request: Request, form: dict[str, str]) -> bool:
    settings = get_settings()
    if not settings.twilio_validate_signatures:
        return True
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        return False
    # Twilio signs the URL it called, which behind a proxy is the public one.
    url = str(request.url).replace("http://", "https://", 1)
    expected = compute_signature(settings.twilio_auth_token, url, form)
    return hmac.compare_digest(expected, signature)


def build_connect_twiml(
    *,
    ws_url: str,
    consent_line: str | None = CONSENT_LINE,
) -> str:
    """`<Connect><Stream>` is bidirectional; `<Start><Stream>` is not.

    `<Connect>` is what lets us write audio back down the same socket, and it
    blocks until the stream ends, which is why nothing follows it.
    """
    say = f"<Say>{escape(consent_line)}</Say>" if consent_line else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{say}"
        "<Connect>"
        f"<Stream url={quoteattr(ws_url)}/>"
        "</Connect>"
        "</Response>"
    )


def build_whisper_twiml(*, from_e164: str, reason: str, summary: str) -> str:
    """Played to the human before the legs are bridged."""
    spoken = f"Call from {_speakable(from_e164)}. Reason: {reason}."
    if summary:
        spoken += f" {summary}"
    safe_spoken = escape(spoken, {'"': "&quot;"})
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say>{safe_spoken}</Say></Response>"
    )


def _speakable(e164: str) -> str:
    """Twilio's TTS reads a run of digits far better spaced out."""
    digits = [c for c in e164 if c.isdigit()]
    return " ".join(digits[-10:]) if digits else "an unknown number"


def media_url(
    *, call_id: uuid.UUID, client_id: str, call_sid: str, from_e164: str, consent: bool
) -> str:
    settings = get_settings()
    query = urlencode(
        {
            "client_id": client_id,
            "call_sid": call_sid,
            "from": from_e164,
            "consent": "1" if consent else "0",
        }
    )
    return f"{settings.websocket_base_url}/media/{call_id}?{query}"


@router.post("/twiml/incoming")
@router.post("/telephony/voice")
async def inbound_voice(request: Request) -> Response:
    """Twilio hits this on every inbound call. It must answer in well under a second."""
    form = {k: str(v) for k, v in (await request.form()).items()}

    if not await validate_twilio_request(request, form):
        log.warning("twilio_signature_rejected", path=str(request.url.path))
        return Response(status_code=403, content="invalid signature")

    to_number = form.get("To", "")
    from_number = form.get("From", "")
    call_sid = form.get("CallSid", "")

    try:
        config = get_registry().resolve_by_number(to_number)
    except ClientConfigNotFound:
        log.warning("inbound_unrouted", to=mask_e164(to_number), call_sid=call_sid)
        return Response(content=REJECT_TWIML, media_type="application/xml")

    # Consent is captured by announcing it; the recording worker refuses to
    # store a URL for any call where this flag is false.
    call_id = uuid.uuid4()
    async with session_scope() as session:
        await repository.create_call(
            session,
            call_id=call_id,
            client_id=config.client_id,
            twilio_call_sid=call_sid or f"unidentified-{call_id}",
            from_e164=from_number,
            consent_captured=True,
        )
    twiml = build_connect_twiml(
        ws_url=media_url(
            call_id=call_id,
            client_id=config.client_id,
            call_sid=call_sid,
            from_e164=from_number,
            consent=True,
        )
    )
    log.info(
        "inbound_call_routed",
        client_id=config.client_id,
        call_sid=call_sid,
        from_e164=mask_e164(from_number),
    )
    return Response(content=twiml, media_type="application/xml")


@router.api_route("/telephony/whisper", methods=["GET", "POST"])
async def whisper(request: Request) -> Response:
    """Context spoken to the human before a warm transfer connects."""
    params = request.query_params
    twiml = build_whisper_twiml(
        from_e164=params.get("from", ""),
        reason=params.get("reason", "no reason given"),
        summary=params.get("summary", ""),
    )
    return Response(content=twiml, media_type="application/xml")
