"""`send_payment_link` — Stripe Payment Link, delivered by SMS.

The agent never hears a card number, never repeats one, and never asks for one.
It sends a link and says so. That is the whole PCI posture: card data does not
enter the audio path, so there is nothing in the transcript to redact and
nothing in the recording to scope. `security/redaction.py` is the backstop for
a caller who reads digits unprompted, not the primary control.

1,500 ms budget, `degrade` on failure. Degraded means "I'll text it over
shortly" — the link is queued for the post-call worker, so the promise still
holds. [P] in the plan: not demo-blocking, but the demo says it exists.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from apps.api.config.schema import ClientConfig
from apps.api.observability.logging import get_logger
from apps.api.security.redaction import mask_e164
from apps.api.settings import get_settings
from apps.api.tools.registry import ToolResult, ToolSpec, failure, ok

log = get_logger(__name__)

SmsSender = Callable[..., Awaitable[bool]]

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "amount_usd": {
            "type": "number",
            "description": "Amount to charge in dollars. Omit to use the configured price.",
        },
        "description": {
            "type": "string",
            "description": "What the payment is for, e.g. 'emergency call-out fee'.",
        },
    },
    "required": [],
    "additionalProperties": False,
}

DEGRADE_HINT = (
    "The payment link did not send. Tell the caller you will text it over shortly — do not "
    "ask for card details and do not read any numbers out loud."
)
NO_NUMBER_HINT = (
    "There is no mobile number for this caller. Ask them to confirm the best number for a "
    "text message, then try again. Never take card details over the phone."
)


async def create_payment_link(
    *,
    amount_cents: int,
    description: str,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """A one-off Payment Link. Returns the URL, or None on any failure."""
    settings = get_settings()
    if not settings.stripe_api_key:
        return None

    # Stripe's form encoding for nested params. One inline price, quantity one.
    form = {
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": str(amount_cents),
        "line_items[0][price_data][product_data][name]": description[:250],
    }
    if settings.stripe_price_id:
        form = {"line_items[0][price]": settings.stripe_price_id, "line_items[0][quantity]": "1"}

    headers = {"Authorization": f"Bearer {settings.stripe_api_key}"}
    url = "https://api.stripe.com/v1/payment_links"
    if client is not None:
        response = await client.post(url, data=form, headers=headers)
    else:
        async with httpx.AsyncClient(timeout=5.0) as owned:
            response = await owned.post(url, data=form, headers=headers)

    if response.status_code >= 400:
        log.warning("stripe_payment_link_failed", status=response.status_code)
        return None
    link = response.json().get("url")
    return str(link) if link else None


async def send_payment_link(
    *,
    config: ClientConfig,
    send_sms: SmsSender,
    from_e164: str = "",
    amount_usd: float | None = None,
    description: str = "Service payment",
    http_client: httpx.AsyncClient | None = None,
    **_: Any,
) -> ToolResult:
    if not from_e164:
        return failure("not_found", NO_NUMBER_HINT, {"reason": "no caller number"})

    amount = amount_usd if amount_usd is not None else config.booking.emergency_fee_usd
    amount_cents = round((amount or 0) * 100)
    if amount_cents <= 0 and not get_settings().stripe_price_id:
        return failure("unavailable", DEGRADE_HINT, {"reason": "no amount and no configured price"})

    link = await create_payment_link(
        amount_cents=amount_cents, description=description, client=http_client
    )
    if link is None:
        return failure("unavailable", DEGRADE_HINT, {"reason": "stripe link creation failed"})

    body = f"{config.display_name}: secure payment link for {description} — {link}"
    delivered = await send_sms(to=from_e164, body=body)
    if not delivered:
        return failure("unavailable", DEGRADE_HINT, {"reason": "sms delivery failed"})

    log.info("payment_link_sent", to=mask_e164(from_e164), amount_cents=amount_cents)
    return ok(
        {
            "sent_to": mask_e164(from_e164),
            "amount_usd": round(amount_cents / 100, 2),
            "description": description,
        }
    )


def spec(config: ClientConfig, send_sms: SmsSender) -> ToolSpec:
    async def handler(**kwargs: Any) -> ToolResult:
        return await send_payment_link(config=config, send_sms=send_sms, **kwargs)

    return ToolSpec(
        name="send_payment_link",
        description=(
            "Text the caller a secure payment link. Use this whenever payment comes up. "
            "Never ask for, accept, or repeat card numbers."
        ),
        json_schema=SCHEMA,
        handler=handler,
        timeout_ms=1500,
        on_failure="degrade",
        filler_phrase="One moment, I'll get that sent over.",
    )
