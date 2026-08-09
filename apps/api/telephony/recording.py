"""Start an explicitly opted-in recording on an in-progress Twilio call."""

from __future__ import annotations

import httpx

from apps.api.observability.logging import get_logger
from apps.api.settings import get_settings

log = get_logger(__name__)


async def start_call_recording(call_sid: str) -> bool:
    """Ask Twilio to record the live call and callback when it is complete.

    Recording is disabled by default. The inbound webhook schedules this after
    it has returned the consent-containing TwiML, so an environment must make
    the legal and operational choice explicitly with `TWILIO_RECORDING_ENABLED`.
    This POST is intentionally single-attempt: creating a second recording is
    worse than leaving the optional recording absent.
    """
    settings = get_settings()
    if not settings.twilio_recording_enabled or not call_sid:
        return False

    url = (
        "https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.twilio_account_sid}/Calls/{call_sid}/Recordings.json"
    )
    callback = f"{settings.public_base_url.rstrip('/')}/telephony/recording"
    data = {
        "RecordingStatusCallback": callback,
        "RecordingStatusCallbackMethod": "POST",
        "RecordingStatusCallbackEvent": "completed absent",
        "RecordingChannels": "dual",
        "RecordingTrack": "both",
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                url,
                data=data,
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            )
    except httpx.HTTPError as exc:
        log.warning("recording_start_error", call_sid=call_sid, error=type(exc).__name__)
        return False

    if response.status_code >= 400:
        log.warning("recording_start_failed", call_sid=call_sid, status=response.status_code)
        return False

    log.info("recording_started", call_sid=call_sid)
    return True
