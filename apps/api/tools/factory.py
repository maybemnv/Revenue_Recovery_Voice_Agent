"""Assemble the six tools for one call.

The registry is built per call rather than per process because every spec closes
over that call's client config, its Cal.com client, and its DB session factory.
`tools_enabled` in the client YAML then narrows it — a client with no CRM simply
does not get the tool, and the model cannot call what it was never told about.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from apps.api.config.schema import ClientConfig
from apps.api.telephony.sms import sms_sender_for
from apps.api.tools import availability, booking, knowledge, payment, service_area, transfer
from apps.api.tools.calcom import CalcomClient
from apps.api.tools.embeddings import embed_text
from apps.api.tools.registry import RegistryBundle, ToolRegistry, ToolSpec

# Everything shipped. `tools_enabled` decides what a given client actually gets.
ALL_TOOL_NAMES = (
    "check_service_area",
    "check_availability",
    "book_appointment",
    "lookup_knowledge",
    "transfer_to_human",
    "send_payment_link",
)


def build_registry(
    config: ClientConfig,
    *,
    session_factory: Any,
    calcom: CalcomClient | None = None,
    embed: Callable[[str], Awaitable[list[float] | None]] | None = None,
) -> ToolRegistry:
    calcom = calcom or CalcomClient()
    specs: list[ToolSpec] = [
        service_area.spec(config),
        availability.spec(config, calcom),
        booking.spec(config, calcom),
        knowledge.spec(session_factory, config.client_id, embed or embed_text),
        transfer.spec(config),
        payment.spec(config, sms_sender_for(config.client_id)),
    ]
    full = ToolRegistry(specs)
    return full.filtered(config.tools_enabled) if config.tools_enabled else full


def build_bundle(
    config: ClientConfig,
    *,
    session_factory: Any,
    call_id: str,
    call_sid: str,
    from_e164: str,
    calcom: CalcomClient | None = None,
    embed: Callable[[str], Awaitable[list[float] | None]] | None = None,
) -> RegistryBundle:
    """Registry plus the context merged into every handler's kwargs.

    Handlers take `**_` so an unused context key is inert; that is what lets one
    context dict serve six different signatures.
    """
    return RegistryBundle(
        registry=build_registry(
            config, session_factory=session_factory, calcom=calcom, embed=embed
        ),
        context={"call_id": call_id, "call_sid": call_sid, "from_e164": from_e164},
    )
