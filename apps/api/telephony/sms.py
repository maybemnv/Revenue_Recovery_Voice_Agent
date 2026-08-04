"""Outbound SMS, with a suppression check in front of every send.

Suppression is checked here rather than at each call site, because "we texted
someone who had replied STOP" is the kind of failure that only needs to happen
once. `is_suppressed` is the authority and this module will not send around it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

from apps.api.db.repository import is_suppressed
from apps.api.db.session import session_scope
from apps.api.observability.logging import get_logger
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
    client: httpx.AsyncClient | None = None,
) -> bool:
    """True only if Twilio accepted the message. Never raises."""
    settings = get_settings()
    if client_id:
        async with session_scope() as session:
            if await is_suppressed(session, client_id=client_id, phone_e164=to):
                log.info("sms_suppressed", to=mask_e164(to), client_id=client_id)
                return False

    if not settings.twilio_account_sid or not settings.twilio_messaging_from:
        log.warning("sms_not_configured", to=mask_e164(to))
        return False

    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    data = {"To": to, "From": settings.twilio_messaging_from, "Body": body}
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    try:
        if client is not None:
            response = await client.post(url, data=data, auth=auth)
        else:
            async with httpx.AsyncClient(timeout=5.0) as owned:
                response = await owned.post(url, data=data, auth=auth)
    except httpx.HTTPError as exc:
        log.warning("sms_send_error", to=mask_e164(to), error=type(exc).__name__)
        return False

    if response.status_code >= 400:
        log.warning("sms_send_failed", to=mask_e164(to), status=response.status_code)
        return False
    log.info("sms_sent", to=mask_e164(to), sid=response.json().get("sid"))
    return True


def sms_sender_for(client_id: str) -> Callable[..., Awaitable[bool]]:
    """Bind a client id so tool handlers can call `send_sms(to=..., body=...)`."""

    async def _send(*, to: str, body: str) -> bool:
        return await send_sms(to=to, body=body, client_id=client_id)

    return _send
