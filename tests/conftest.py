"""Shared fixtures. Nothing here touches a network or a database."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

# Set before any app import: `get_settings()` is lru_cached on first call.
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACtest")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test-token")
os.environ.setdefault("TWILIO_MESSAGING_FROM", "+13125550100")
os.environ.setdefault("CALCOM_EVENT_TYPE_ID", "12345")

from apps.api.config.schema import ClientConfig


@pytest.fixture
def client_config_dict() -> dict[str, Any]:
    return {
        "client_id": "demo-hvac",
        "display_name": "Demo HVAC",
        "phone_number": "+13125550123",
        "timezone": "America/Chicago",
        "realtime": {
            "model": "gpt-realtime-2.1",
            "voice": "cedar",
            "instructions": "You are a receptionist for Demo HVAC.",
            "greeting": "Thanks for calling Demo HVAC.",
        },
        "hours": {
            "regular": {"mon_fri": "08:00-18:00", "sat": "09:00-13:00", "sun": "closed"},
            "emergency_dispatch": "always",
        },
        "service_area": {
            "postcodes": ["60601", "60602"],
            "out_of_area_action": "capture_and_refer",
        },
        "tools_enabled": [
            "check_service_area",
            "check_availability",
            "book_appointment",
            "lookup_knowledge",
            "transfer_to_human",
        ],
        "escalation": {
            "safety_keywords": ["gas leak", "carbon monoxide", "smell gas", "fire"],
            "target_number": "+13125559999",
            "max_consecutive_tool_failures": 3,
            "max_negative_sentiment_turns": 2,
        },
        "budget": {"max_call_seconds": 480, "max_call_cost_usd": 1.20, "wrap_up_at_pct": 80},
        "booking": {
            "event_type_id": 42,
            "default_duration_minutes": 60,
            "emergency_fee_usd": 149.0,
        },
    }


@pytest.fixture
def config(client_config_dict: dict[str, Any]) -> ClientConfig:
    return ClientConfig.model_validate(client_config_dict)


class FakeSocket:
    """Records everything sent, so ordering assertions read as a list."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    def types(self) -> list[str]:
        return [p.get("type") or p.get("event", "") for p in self.sent]


@pytest.fixture
def fake_socket() -> Iterator[FakeSocket]:
    yield FakeSocket()
