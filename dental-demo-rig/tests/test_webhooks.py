"""The webhook surface, exercised the way Retell calls it."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webhooks.app import app
from webhooks.store import MemoryStore, set_store


@pytest.fixture
def client() -> TestClient:
    set_store(MemoryStore())
    with TestClient(app) as test_client:
        test_client.post("/demo/_showcase/seed")
        yield test_client
    set_store(None)


def _call(name: str, args: dict) -> dict:
    """Retell's custom-function envelope."""
    return {"call": {"call_id": "call_test", "agent_id": "agent_test"}, "name": name, "args": args}


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_seed_produces_a_mixed_calendar(client: TestClient) -> None:
    body = client.post("/demo/_showcase/seed").json()
    assert body["slots"] > 0
    # Neither wall-to-wall open (reads as a practice with no patients) nor full.
    assert 0 < body["open"] < body["slots"]


def test_check_insurance_accepted(client: TestClient) -> None:
    response = client.post(
        "/tools/check_insurance",
        params={"prospect": "_showcase"},
        json=_call("check_insurance", {"carrier": "Delta Dental"}),
    )
    body = response.json()
    assert body["status"] == "accepted"
    assert body["disclaimer"]


def test_check_insurance_out_of_network(client: TestClient) -> None:
    body = client.post(
        "/tools/check_insurance",
        params={"prospect": "_showcase"},
        json=_call("check_insurance", {"carrier": "Humana"}),
    ).json()
    assert body["status"] == "out_of_network"


def test_bare_argument_shape_is_accepted(client: TestClient) -> None:
    """Retell has shipped both `{args: {...}}` and a bare argument object."""
    body = client.post(
        "/tools/check_insurance",
        params={"prospect": "_showcase"},
        json={"carrier": "Cigna"},
    ).json()
    assert body["status"] == "accepted"


def test_find_then_book(client: TestClient) -> None:
    found = client.post(
        "/tools/find_appointment",
        params={"prospect": "_showcase"},
        json=_call("find_appointment", {"appointment_type": "Cleaning / recall"}),
    ).json()
    assert found["found"]

    slot_id = found["slots"][0]["slot_id"]
    booked = client.post(
        "/tools/book_appointment",
        params={"prospect": "_showcase"},
        json=_call("book_appointment", {"slot_id": slot_id, "patient_name": "Sam Reed"}),
    ).json()
    assert booked["booked"]
    assert booked["confirmation"]

    # The slot must now read as taken on the demo page.
    calendar = client.get("/demo/_showcase/calendar").json()
    assert next(s for s in calendar["slots"] if s["id"] == slot_id)["status"] == "booked"


def test_book_without_slot_id_refuses(client: TestClient) -> None:
    body = client.post(
        "/tools/book_appointment",
        params={"prospect": "_showcase"},
        json=_call("book_appointment", {}),
    ).json()
    assert body["booked"] is False
    assert body["reason_code"] == "missing_slot_id"


def test_tools_answer_within_the_latency_budget(client: TestClient) -> None:
    """Retell waits on these mid-conversation; over ~300 ms is audible dead air."""
    for path, args in (
        ("/tools/check_insurance", {"carrier": "Delta Dental"}),
        ("/tools/find_appointment", {"appointment_type": "Cleaning / recall"}),
        ("/tools/answer_from_kb", {"question": "What are your hours?"}),
    ):
        body = client.post(path, params={"prospect": "_showcase"}, json=_call("x", args)).json()
        assert body["_latency_ms"] < 300, f"{path} took {body['_latency_ms']}ms"


def test_unknown_prospect_is_404(client: TestClient) -> None:
    response = client.post(
        "/tools/check_insurance",
        params={"prospect": "nope"},
        json=_call("check_insurance", {"carrier": "Delta Dental"}),
    )
    assert response.status_code == 404


def test_post_call_webhook_records_only_final_events(client: TestClient) -> None:
    from webhooks.store import get_store

    payload = {
        "event": "call_started",
        "call": {"call_id": "c1", "agent_id": "agent_test", "transcript": ""},
    }
    assert client.post("/retell/post-call", json=payload).json()["status"] == "ignored"

    payload = {
        "event": "call_analyzed",
        "call": {
            "call_id": "c1",
            "agent_id": "agent_test",
            "transcript": "Agent: Thanks for calling…",
            "call_analysis": {"call_summary": "Booked a cleaning.", "user_sentiment": "Positive"},
        },
    }
    assert client.post("/retell/post-call", json=payload).json()["status"] == "recorded"
    calls = get_store().calls  # type: ignore[attr-defined]
    assert calls[0]["summary"] == "Booked a cleaning."
