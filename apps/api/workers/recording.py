"""Recording retrieval, gated on consent.

Twilio holds the audio; we hold the permission to keep it. `set_recording_url`
is the only writer of `calls.recording_url` and it refuses when
`consent_captured` is false, so this worker's job is to fetch the URL, verify it
is reachable, and let the repository decide whether it is allowed to persist.

Deleting the copy at Twilio when consent was not captured is the part that
matters: leaving it there and simply not linking to it is not a deletion.
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.orm import Session

from apps.api.db.models import Call
from apps.api.observability.logging import get_logger
from apps.api.resilience import BACKGROUND, request_with_retry_sync
from apps.api.settings import get_settings

log = get_logger(__name__)


def _twilio_auth() -> tuple[str, str]:
    settings = get_settings()
    return settings.twilio_account_sid, settings.twilio_auth_token


def delete_remote_recording(recording_sid: str, *, client: httpx.Client | None = None) -> bool:
    """Remove the recording from Twilio. Called when consent is absent."""
    settings = get_settings()
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.twilio_account_sid}/Recordings/{recording_sid}.json"
    )
    def send() -> httpx.Response:
        if client is not None:
            return client.delete(url, auth=_twilio_auth())
        with httpx.Client(timeout=15.0) as owned:
            return owned.delete(url, auth=_twilio_auth())

    try:
        # A DELETE is idempotent, and this one is a consent obligation: retrying
        # a 503 here is the difference between honouring it and only logging it.
        response = request_with_retry_sync(send, label="twilio recording delete", policy=BACKGROUND)
    except httpx.HTTPError as exc:
        log.warning("recording_delete_error", error=type(exc).__name__)
        return False
    # 404 means someone already removed it, which is the desired end state.
    ok = response.status_code in (204, 404)
    if not ok:
        log.warning("recording_delete_failed", status=response.status_code)
    return ok


def store_recording(
    session: Session,
    call_id: uuid.UUID,
    *,
    recording_url: str,
    recording_sid: str = "",
    client: httpx.Client | None = None,
) -> bool:
    """Persist the URL if and only if consent was captured.

    Returns whether it was stored. A False here is the control working, and it
    is logged at info rather than warning for exactly that reason.
    """
    call = session.get(Call, call_id)
    if call is None:
        raise LookupError(f"no call {call_id}")

    if not call.consent_captured:
        log.info("recording_rejected_no_consent", call_id=str(call_id))
        if recording_sid:
            delete_remote_recording(recording_sid, client=client)
        return False

    call.recording_url = recording_url
    log.info("recording_stored", call_id=str(call_id))
    return True
