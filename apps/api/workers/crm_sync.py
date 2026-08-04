"""Push the call and its contact into the CRM after the call has ended.

Off the critical path on purpose. A CRM write that takes four seconds is fine
here and unacceptable mid-call, and a HubSpot outage should cost a delayed sync,
never a dropped booking.

Both writes dedupe: the local `contacts` row on the UNIQUE constraint, and the
remote contact on E.164 via the adapter's search-then-write. That is what makes
this safe to redeliver under `acks_late`.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from apps.api.db.models import Call, CallAnalysis, Contact
from apps.api.observability.logging import get_logger
from apps.api.security.redaction import mask_e164
from apps.api.tools.crm import CRMPort, build_crm

log = get_logger(__name__)


def sync_call(session: Session, call_id: uuid.UUID, *, crm: CRMPort | None = None) -> str | None:
    """Mirror one finished call into the CRM. Returns the remote contact id."""
    call = session.get(Call, call_id)
    if call is None:
        raise LookupError(f"no call {call_id}")

    crm = crm or build_crm()
    analysis = session.get(CallAnalysis, call_id)
    summary = analysis.summary if analysis else "Voice agent call — no analysis available."
    name = session.scalar(
        select(Contact.full_name).where(
            Contact.client_id == call.client_id, Contact.phone_e164 == call.from_e164
        )
    )

    # The adapters are async because the call path may want them one day; here
    # they are driven from a sync worker, so one loop per task is the price.
    contact = asyncio.run(crm.upsert_contact(phone_e164=call.from_e164, full_name=name))

    duration = int((call.ended_at - call.started_at).total_seconds()) if call.ended_at else 0
    asyncio.run(
        crm.log_call(
            crm_id=contact.crm_id,
            summary=summary,
            outcome=call.outcome or "unknown",
            duration_seconds=duration,
        )
    )

    if contact.crm_id:
        # Mirror the remote id locally so the next sync and the dashboard can
        # both link straight through to the CRM record.
        stmt = pg_insert(Contact).values(
            id=uuid.uuid4(),
            client_id=call.client_id,
            phone_e164=call.from_e164,
            crm_id=contact.crm_id,
        )
        session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_contacts_client_phone",
                set_={"crm_id": stmt.excluded.crm_id},
            )
        )

    log.info(
        "crm_synced",
        call_id=str(call_id),
        crm_id=contact.crm_id or None,
        from_e164=mask_e164(call.from_e164),
    )
    return contact.crm_id or None
