"""`check_service_area` — postcode lookup against the client's own config.

100 ms budget, `escalate` on failure. There is no network call here; a failure
means the config registry is broken, which is not something to degrade around.
The p99 is dominated by the set membership test, so it is measured in
microseconds, and it is below the 250 ms filler threshold on purpose.
"""

from __future__ import annotations

import re
from typing import Any

from apps.api.config.schema import ClientConfig
from apps.api.tools.registry import ToolResult, ToolSpec, ok

_POSTCODE_IN_TEXT = re.compile(r"\b(\d{5})(?:-\d{4})?\b")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "postcode": {
            "type": "string",
            "description": "5-digit US postcode, if the caller stated one.",
        },
        "address": {
            "type": "string",
            "description": "Street address as spoken. Used only to recover a postcode.",
        },
    },
    "required": [],
    "additionalProperties": False,
}


def extract_postcode(postcode: str | None, address: str | None) -> str | None:
    if postcode:
        match = _POSTCODE_IN_TEXT.search(postcode)
        if match:
            return match.group(1)
    if address:
        match = _POSTCODE_IN_TEXT.search(address)
        if match:
            return match.group(1)
    return None


async def check_service_area(
    *,
    config: ClientConfig,
    postcode: str | None = None,
    address: str | None = None,
    **_: Any,
) -> ToolResult:
    resolved = extract_postcode(postcode, address)
    if resolved is None:
        # `not_found` rather than a failure: the caller simply has not said
        # enough yet, and the agent should ask rather than apologise.
        return {
            "status": "not_found",
            "data": {"in_area": None},
            "speak_hint": "Ask the caller for their postcode or street address.",
        }

    in_area = resolved in set(config.service_area.postcodes)
    return ok(
        {
            "in_area": in_area,
            "postcode": resolved,
            "out_of_area_action": config.service_area.out_of_area_action,
        }
    )


def spec(config: ClientConfig) -> ToolSpec:
    async def handler(**kwargs: Any) -> ToolResult:
        return await check_service_area(config=config, **kwargs)

    return ToolSpec(
        name="check_service_area",
        description=(
            "Check whether an address or postcode is inside the business's service area. "
            "Call this before discussing appointments."
        ),
        json_schema=SCHEMA,
        handler=handler,
        timeout_ms=100,
        on_failure="escalate",
        filler_phrase=None,  # under the 250 ms threshold; masking it sounds slower
    )
