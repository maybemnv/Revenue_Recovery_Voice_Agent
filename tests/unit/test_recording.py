from __future__ import annotations

from types import SimpleNamespace

import httpx
import respx

from apps.api.telephony import recording


def _settings(**overrides: object) -> SimpleNamespace:
    values = {
        "twilio_recording_enabled": True,
        "twilio_account_sid": "AC123",
        "twilio_auth_token": "auth-token",
        "public_base_url": "https://voice.example.test/",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@respx.mock
async def test_start_call_recording_posts_callback_and_dual_track(monkeypatch) -> None:
    monkeypatch.setattr(recording, "get_settings", lambda: _settings())
    route = respx.post(
        "https://api.twilio.com/2010-04-01/Accounts/AC123/Calls/CA456/Recordings.json"
    ).mock(return_value=httpx.Response(201, json={"sid": "RE789"}))

    assert await recording.start_call_recording("CA456") is True
    request = route.calls[0].request
    assert request.headers["authorization"].startswith("Basic ")
    body = request.content.decode()
    assert (
        "RecordingStatusCallback=https%3A%2F%2Fvoice.example.test%2Ftelephony%2Frecording"
        in body
    )
    assert "RecordingStatusCallbackEvent=completed+absent" in body
    assert "RecordingChannels=dual" in body
    assert "RecordingTrack=both" in body


@respx.mock
async def test_start_call_recording_is_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(
        recording, "get_settings", lambda: _settings(twilio_recording_enabled=False)
    )

    assert await recording.start_call_recording("CA456") is False
    assert not respx.calls
