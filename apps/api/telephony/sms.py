"""Outbound SMS, with a suppression check in front of every send.

Suppression is checked here rather than at each call site, because "we texted
someone who had replied STOP" is the kind of failure that only needs to happen
once. `is_suppressed` is the authority and this module will not send around it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

from apps.api.db.repository import claim_sms_send, is_suppressed, mark_sms_delivered
from apps.api.db.session import session_scope
from apps.api.observability.logging import get_logger
from apps.api.resilience import UNSAFE_WRITE, request_with_retry
from apps.api.security.redaction import mask_e164
from apps.api.settings import get_settings

log = get_logger(__name__)

OPT_OUT_KEYWORDS = frozenset(
    {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "revoke", "optout", "opt-out"}
)


def is_opt_out(body: str) -> bool:
    """Twilio handles STOP for itself, but we mirror it so our own sends stop too."""
    return body.strip().lower().strip(".!") in OPT_OUT_KEYWORDS


async def send_sms(
    *,
    to: str,
    body: str,
    client_id: str | None = None,
    dedupe_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """True only if Twilio accepted the message. Never raises.

    With a `dedupe_key`, the send first claims `(client_id, dedupe_key)` in the
    database; a replay that loses the claim returns False without touching the
    provider. The claim is not released on failure — a retry storm must not turn
    one failed send into a duplicate later. Without a key, the old at-most-once
    behaviour stands.
    """
    settings = get_settings()
    if not settings.twilio_account_sid or not settings.twilio_messaging_from:
        # Do this before opening a claim transaction. Configuration can be
        # repaired and replayed later; an unconfigured attempt never reached
        # Twilio and must not permanently consume its dedupe key.
        log.warning("sms_not_configured", to=mask_e164(to))
        return False

    if client_id:
        async with session_scope() as session:
            if await is_suppressed(session, client_id=client_id, phone_e164=to):
                log.info("sms_suppressed", to=mask_e164(to), client_id=client_id)
                return False

            if dedupe_key:
                claim = await claim_sms_send(
                    session, client_id=client_id, to_e164=to, dedupe_key=dedupe_key
                )
                if claim is None:
                    log.info("sms_deduplicated", to=mask_e164(to), key=dedupe_key)
                    return False
                send_id = claim

    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    data = {"To": to, "From": settings.twilio_messaging_from, "Body": body}
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    async def send() -> httpx.Response:
        if client is not None:
            return await client.post(url, data=data, auth=auth)
        async with httpx.AsyncClient(timeout=5.0) as owned:
            return await owned.post(url, data=data, auth=auth)

    try:
        # UNSAFE_WRITE, not IN_CALL: there is no idempotency key on a Twilio
        # send, so only a refusal or a failed connect is retried. An ambiguous
        # timeout is left alone rather than risk a duplicate text.
        response = await request_with_retry(send, label="twilio sms", policy=UNSAFE_WRITE)
    except httpx.HTTPError as exc:
        log.warning("sms_send_error", to=mask_e164(to), error=type(exc).__name__)
        return False

    if response.status_code >= 400:
        log.warning("sms_send_failed", to=mask_e164(to), status=response.status_code)
        return False

    sid = response.json().get("sid")
    if client_id and dedupe_key:
        async with session_scope() as session:
            await mark_sms_delivered(session, send_id=send_id, provider_sid=sid)
    log.info("sms_sent", to=mask_e164(to), sid=sid)
    return True


def sms_sender_for(client_id: str) -> Callable[..., Awaitable[bool]]:
    """Bind a client id so tool handlers can call `send_sms(to=..., body=...)`."""

    async def _send(*, to: str, body: str, dedupe_key: str | None = None) -> bool:
        return await send_sms(to=to, body=body, client_id=client_id, dedupe_key=dedupe_key)

    return _send
