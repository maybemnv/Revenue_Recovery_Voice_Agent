"""CRM port, plus a HubSpot adapter.

The port exists so the call path never imports HubSpot. Swapping to Salesforce
for a different client is a new adapter and a config line, not a change to
anything that touches audio.

Dedupe is on E.164, matched to the `uq_contacts_client_phone` constraint on our
side. HubSpot's own search is authoritative for the remote id; our table is the
local mirror that post-call sync reconciles against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from apps.api.observability.logging import get_logger
from apps.api.security.redaction import mask_e164
from apps.api.settings import get_settings

log = get_logger(__name__)

# HubSpot's default association type for call -> contact.
CALL_TO_CONTACT_ASSOCIATION_TYPE_ID = 194


@dataclass(frozen=True, slots=True)
class CRMContact:
    crm_id: str
    phone_e164: str
    full_name: str | None = None
    email: str | None = None
    created: bool = False


@runtime_checkable
class CRMPort(Protocol):
    """What the call path is allowed to know about a CRM."""

    async def find_by_phone(self, phone_e164: str) -> CRMContact | None: ...

    async def upsert_contact(
        self, *, phone_e164: str, full_name: str | None = None, email: str | None = None
    ) -> CRMContact: ...

    async def log_call(
        self, *, crm_id: str, summary: str, outcome: str, duration_seconds: int
    ) -> str | None: ...


class NullCRM:
    """Used when no CRM is configured. Never fails, never pretends to succeed."""

    async def find_by_phone(self, phone_e164: str) -> CRMContact | None:
        return None

    async def upsert_contact(
        self, *, phone_e164: str, full_name: str | None = None, email: str | None = None
    ) -> CRMContact:
        return CRMContact(crm_id="", phone_e164=phone_e164, full_name=full_name, email=email)

    async def log_call(
        self, *, crm_id: str, summary: str, outcome: str, duration_seconds: int
    ) -> str | None:
        return None


def _split_name(full_name: str | None) -> tuple[str, str]:
    if not full_name:
        return "", ""
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


class HubSpotCRM:
    """HubSpot v3 CRM objects. Contacts and call engagements only."""

    def __init__(
        self,
        *,
        access_token: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        self._token = access_token if access_token is not None else settings.hubspot_access_token
        self._base = (base_url or settings.hubspot_api_base).rstrip("/")
        self._client = client

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self._base}{path}"
        if self._client is not None:
            return await self._client.request(method, url, headers=self._headers, **kwargs)
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.request(method, url, headers=self._headers, **kwargs)

    async def find_by_phone(self, phone_e164: str) -> CRMContact | None:
        response = await self._request(
            "POST",
            "/crm/v3/objects/contacts/search",
            json={
                "filterGroups": [
                    {"filters": [{"propertyName": "phone", "operator": "EQ", "value": phone_e164}]}
                ],
                "properties": ["phone", "firstname", "lastname", "email"],
                "limit": 1,
            },
        )
        if response.status_code >= 400:
            log.warning("hubspot_search_failed", status=response.status_code)
            return None
        results = response.json().get("results", [])
        return _to_contact(results[0], phone_e164) if results else None

    async def upsert_contact(
        self, *, phone_e164: str, full_name: str | None = None, email: str | None = None
    ) -> CRMContact:
        """Search-then-write. HubSpot has no upsert on a non-unique property."""
        existing = await self.find_by_phone(phone_e164)
        first, last = _split_name(full_name)
        properties: dict[str, str] = {"phone": phone_e164}
        if first:
            properties["firstname"] = first
        if last:
            properties["lastname"] = last
        if email:
            properties["email"] = email

        if existing is not None:
            response = await self._request(
                "PATCH",
                f"/crm/v3/objects/contacts/{existing.crm_id}",
                json={"properties": properties},
            )
            created = False
        else:
            response = await self._request(
                "POST", "/crm/v3/objects/contacts", json={"properties": properties}
            )
            created = True

        if response.status_code >= 400:
            log.warning(
                "hubspot_upsert_failed",
                status=response.status_code,
                phone=mask_e164(phone_e164),
            )
            if existing is not None:
                return existing
            raise RuntimeError(f"hubspot contact write failed: {response.status_code}")

        written = _to_contact(response.json(), phone_e164)
        log.info("crm_contact_upserted", crm_id=written.crm_id, created=created)
        return CRMContact(
            crm_id=written.crm_id,
            phone_e164=phone_e164,
            full_name=full_name or written.full_name,
            email=email or written.email,
            created=created,
        )

    async def log_call(
        self, *, crm_id: str, summary: str, outcome: str, duration_seconds: int
    ) -> str | None:
        """Attach the call as a timeline engagement. Best-effort by design."""
        if not crm_id:
            return None
        response = await self._request(
            "POST",
            "/crm/v3/objects/calls",
            json={
                "properties": {
                    "hs_call_body": summary,
                    "hs_call_direction": "INBOUND",
                    "hs_call_status": "COMPLETED",
                    "hs_call_duration": str(duration_seconds * 1000),
                    "hs_call_title": f"Voice agent — {outcome}",
                },
                "associations": [
                    {
                        "to": {"id": crm_id},
                        "types": [
                            {
                                "associationCategory": "HUBSPOT_DEFINED",
                                "associationTypeId": CALL_TO_CONTACT_ASSOCIATION_TYPE_ID,
                            }
                        ],
                    }
                ],
            },
        )
        if response.status_code >= 400:
            log.warning("hubspot_call_log_failed", status=response.status_code)
            return None
        return str(response.json().get("id", "")) or None


def _to_contact(payload: dict[str, Any], fallback_phone: str) -> CRMContact:
    props = payload.get("properties") or {}
    name = " ".join(p for p in (props.get("firstname"), props.get("lastname")) if p).strip()
    return CRMContact(
        crm_id=str(payload.get("id", "")),
        phone_e164=props.get("phone") or fallback_phone,
        full_name=name or None,
        email=props.get("email"),
    )


def build_crm() -> CRMPort:
    """The one place that decides which adapter the process uses."""
    settings = get_settings()
    if settings.hubspot_access_token:
        return HubSpotCRM()
    log.info("crm_disabled_no_token")
    return NullCRM()
