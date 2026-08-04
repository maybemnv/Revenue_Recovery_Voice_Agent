"""`transfer_to_human` — warm transfer to a real person.

Warm, not blind: the human hears a whisper with the caller's number and the
reason before the legs are bridged, so they pick up already knowing why. The
whisper is served by `/telephony/whisper`, which reads the context off the query
string rather than out of shared state — the transfer must survive this worker
process dying between the redirect and Twilio's callback.

3,000 ms budget, `escalate` on failure, and deliberately no retry: the registry
only grants a second attempt to `retry_once` and `degrade`. Redirecting a live
call twice is how a caller ends up hearing hold music from two places at once.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode
from xml.sax.saxutils import escape

import httpx

from apps.api.config.schema import ClientConfig
from apps.api.observability.logging import get_logger
from apps.api.security.redaction import mask_e164
from apps.api.settings import get_settings
from apps.api.tools.registry import ToolResult, ToolSpec, failure, ok

log = get_logger(__name__)

TRANSFER_TIMEOUT_SECONDS = 25

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {
            "type": "string",
            "description": "Why this needs a person, in one short phrase the human will hear.",
        },
        "summary": {
            "type": "string",
            "description": "One sentence of context: who is calling and what they need.",
        },
    },
    "required": ["reason"],
    "additionalProperties": False,
}

NO_TARGET_HINT = (
    "There is no transfer number configured for this business. Apologise, take the caller's "
    "name and number, and promise a callback within fifteen minutes."
)
FAILED_HINT = (
    "The transfer did not connect. Apologise, take the caller's name and number, and promise "
    "a callback within fifteen minutes. Do not say they are being transferred."
)


def whisper_url(*, base_url: str, from_e164: str, reason: str, summary: str) -> str:
    query = urlencode({"from": from_e164, "reason": reason, "summary": summary})
    return f"{base_url.rstrip('/')}/telephony/whisper?{query}"


def build_transfer_twiml(*, target: str, whisper: str, caller_id: str | None = None) -> str:
    """TwiML that replaces the agent leg with a bridged, whispered call."""
    dial_attrs = f'timeout="{TRANSFER_TIMEOUT_SECONDS}"'
    if caller_id:
        dial_attrs += f' callerId="{escape(caller_id, {chr(34): "&quot;"})}"'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Dial {dial_attrs}>"
        f'<Number url="{escape(whisper, {chr(34): "&quot;"})}">{escape(target)}</Number>'
        "</Dial>"
        "</Response>"
    )


async def redirect_call(
    *, call_sid: str, twiml: str, client: httpx.AsyncClient | None = None
) -> bool:
    """POST the new TwiML onto the in-progress call. True if Twilio accepted it."""
    settings = get_settings()
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.twilio_account_sid}/Calls/{call_sid}.json"
    )
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    data = {"Twiml": twiml}
    if client is not None:
        response = await client.post(url, data=data, auth=auth)
    else:
        async with httpx.AsyncClient(timeout=5.0) as owned:
            response = await owned.post(url, data=data, auth=auth)
    if response.status_code >= 400:
        log.warning("transfer_redirect_failed", status=response.status_code, call_sid=call_sid)
        return False
    return True


async def transfer_to_human(
    *,
    config: ClientConfig,
    call_sid: str,
    from_e164: str = "",
    reason: str = "caller request",
    summary: str = "",
    http_client: httpx.AsyncClient | None = None,
    **_: Any,
) -> ToolResult:
    target = config.escalation.target_number
    if not target:
        return failure("unavailable", NO_TARGET_HINT, {"reason": "no target_number configured"})
    if not call_sid:
        return failure("unavailable", FAILED_HINT, {"reason": "no call_sid in context"})

    settings = get_settings()
    twiml = build_transfer_twiml(
        target=target,
        whisper=whisper_url(
            base_url=settings.public_base_url,
            from_e164=from_e164,
            reason=reason,
            summary=summary,
        ),
        caller_id=config.phone_number,
    )
    accepted = await redirect_call(call_sid=call_sid, twiml=twiml, client=http_client)
    if not accepted:
        return failure("unavailable", FAILED_HINT, {"reason": "twilio rejected the redirect"})

    log.info(
        "transfer_initiated",
        call_sid=call_sid,
        target=mask_e164(target),
        reason=reason,
    )
    return ok({"target": mask_e164(target), "reason": reason, "warm": True})


def spec(config: ClientConfig) -> ToolSpec:
    async def handler(**kwargs: Any) -> ToolResult:
        return await transfer_to_human(config=config, **kwargs)

    return ToolSpec(
        name="transfer_to_human",
        description=(
            "Connect the caller to a human. Use when the caller asks for a person, when "
            "something is unsafe, or when you cannot help. Say you are connecting them first."
        ),
        json_schema=SCHEMA,
        handler=handler,
        timeout_ms=3000,
        on_failure="escalate",
        filler_phrase="Let me get someone on the line for you.",
    )
