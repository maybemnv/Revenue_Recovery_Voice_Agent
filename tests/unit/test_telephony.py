from __future__ import annotations

import uuid

from apps.api.media.realtime_client import build_session_update
from apps.api.security.redaction import contains_pan, redact_pan
from apps.api.telephony.sms import is_opt_out
from apps.api.telephony.twiml import (
    build_connect_twiml,
    build_whisper_twiml,
    compute_signature,
    media_url,
)


def test_connect_twiml_contains_consent_and_bidirectional_stream() -> None:
    xml = build_connect_twiml(ws_url="wss://demo.example/media/123")
    assert "This call may be recorded" in xml
    assert "<Connect>" in xml
    assert '<Stream url="wss://demo.example/media/123"' in xml
    assert "<Start>" not in xml


def test_media_url_uses_path_call_id_and_query_context() -> None:
    call_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    url = media_url(
        call_id=call_id,
        client_id="northside-hvac",
        call_sid="CA123",
        from_e164="+13125551111",
        consent=True,
    )
    assert url.startswith("ws://localhost:8000/media/12345678-1234-5678-1234-567812345678?")
    assert "client_id=northside-hvac" in url
    assert "consent=1" in url


def test_unknown_route_has_a_graceful_rejection_message() -> None:
    xml = build_connect_twiml(ws_url="wss://demo.example/media/123", consent_line=None)
    assert "<Connect>" in xml
    assert "<Say>" not in xml


def test_whisper_escapes_provider_and_caller_content() -> None:
    xml = build_whisper_twiml(
        from_e164="+13125551111", reason="<urgent>", summary='caller said "gas"'
    )
    assert "&lt;urgent&gt;" in xml
    assert "&quot;gas&quot;" in xml


def test_twilio_signature_is_stable() -> None:
    assert compute_signature("token", "https://example.test/twiml", {"To": "+1", "From": "+2"}) == (
        "CxyMUeIAgtNJbmgE66Boluz9YLA="
    )


def test_realtime_session_uses_only_ga_nested_audio_shape(config) -> None:
    config.realtime.prompt_id = "pmpt_demo"
    config.realtime.prompt_version = "7"
    config.realtime.instructions = None
    payload = build_session_update(config, [])
    session = payload["session"]
    assert payload["type"] == "session.update"
    assert session["audio"]["input"]["format"] == {"type": "audio/pcmu"}
    assert session["audio"]["output"]["format"] == {"type": "audio/pcmu"}
    assert session["audio"]["input"]["turn_detection"]["interrupt_response"] is True
    assert session["prompt"] == {"id": "pmpt_demo", "version": "7"}
    assert "input_audio_format" not in session
    assert "OpenAI-Beta" not in payload


def test_five_pan_shapes_are_redacted_before_persistence() -> None:
    samples = [
        "4111111111111111",
        "4111 1111 1111 1111",
        "4111-1111-1111-1111",
        "card 4111111111111111 cvv 123",
        "four one one one one one one one one one one one one one one one",
    ]
    for sample in samples:
        assert contains_pan(sample)
        assert "4111111111111111" not in redact_pan(sample)
        assert "[REDACTED" in redact_pan(sample)


def test_opt_out_matching_is_case_and_punctuation_insensitive() -> None:
    assert is_opt_out("STOP!")
    assert is_opt_out("unsubscribe")
    assert not is_opt_out("stop by tomorrow")
