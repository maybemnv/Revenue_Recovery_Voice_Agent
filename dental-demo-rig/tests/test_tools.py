"""The mock tools. `check_insurance` gets the most coverage because it is the
one whose failure mode loses a deal rather than looking untidy."""

from __future__ import annotations

import json

import pytest

from clone.profile import PracticeProfile
from webhooks.store import MemoryStore, SlotUnavailable
from webhooks.tools import (
    VERIFY_DISCLAIMER,
    answer_from_kb,
    book_appointment,
    check_insurance,
    find_appointment,
    normalize_carrier,
)

COVERAGE_CLAIMS = ("you're covered", "you are covered", "your plan covers", "that's covered")


# ---------------------------------------------------------------------------
# check_insurance
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "said",
    [
        "Delta Dental",
        "Delta",
        "delta dental ppo",
        "I have Delta Dental insurance",
        "DELTA DENTAL",
    ],
)
def test_accepted_carrier_variants_all_resolve(profile: PracticeProfile, said: str) -> None:
    result = check_insurance(profile, said)
    assert result["status"] == "accepted"
    assert result["carrier"] == "Delta Dental"


@pytest.mark.parametrize("said", ["Humana", "UnitedHealthcare", "Medicaid", "Blue Cross"])
def test_unaccepted_carrier_is_out_of_network(profile: PracticeProfile, said: str) -> None:
    result = check_insurance(profile, said)
    assert result["status"] == "out_of_network"
    assert result["in_network"] is False
    # The agent still needs the accepted list so it can name an alternative.
    assert profile.insurance_accepted == result["accepted_carriers"]


def test_no_carrier_named_returns_unknown(profile: PracticeProfile) -> None:
    assert check_insurance(profile, "")["status"] == "unknown"
    assert check_insurance(profile, "   ")["status"] == "unknown"


def test_practice_with_no_published_carriers_never_claims_acceptance(
    profile: PracticeProfile,
) -> None:
    profile.insurance_accepted = []
    result = check_insurance(profile, "Delta Dental")
    assert result["status"] == "unknown"
    assert result["in_network"] is False


@pytest.mark.parametrize("said", ["Delta Dental", "Humana", "", "Cigna", "Medicaid"])
def test_never_confirms_coverage(profile: PracticeProfile, said: str) -> None:
    """The rule the whole demo rests on: network status, never coverage."""
    blob = json.dumps(check_insurance(profile, said)).lower()
    assert not [claim for claim in COVERAGE_CLAIMS if claim in blob]


@pytest.mark.parametrize("said", ["Delta Dental", "Humana", "Cigna", ""])
def test_always_defers_verification(profile: PracticeProfile, said: str) -> None:
    assert check_insurance(profile, said)["disclaimer"] == VERIFY_DISCLAIMER


def test_normalize_strips_plan_noise() -> None:
    assert normalize_carrier("Delta Dental PPO") == normalize_carrier("delta")
    assert normalize_carrier("I have MetLife dental insurance") == "metlife"


# ---------------------------------------------------------------------------
# find_appointment / book_appointment
# ---------------------------------------------------------------------------
def test_cleaning_routes_to_a_hygienist(profile: PracticeProfile, store: MemoryStore) -> None:
    result = find_appointment(profile, store, "Cleaning / recall")
    assert result["found"]
    providers = {s["provider"] for s in result["slots"]}
    assert providers == {"Melissa"}


def test_new_patient_exam_routes_to_a_dentist(
    profile: PracticeProfile, store: MemoryStore
) -> None:
    result = find_appointment(profile, store, "new patient exam")
    assert result["found"]
    assert {s["provider"] for s in result["slots"]} <= {"Dr. Sarah Chen", "Dr. Raj Patel"}


def test_offers_at_most_three_slots(profile: PracticeProfile, store: MemoryStore) -> None:
    assert len(find_appointment(profile, store, "Cleaning / recall")["slots"]) <= 3


def test_no_availability_never_invents_a_time(
    profile: PracticeProfile, store: MemoryStore
) -> None:
    for slot in store.slots.values():
        slot["status"] = "booked"
    result = find_appointment(profile, store, "Cleaning / recall")
    assert result["found"] is False
    assert result["slots"] == []
    assert "do not invent" in result["speak_hint"].lower()


def test_booking_claims_the_slot(profile: PracticeProfile, store: MemoryStore) -> None:
    slot_id = find_appointment(profile, store, "Cleaning / recall")["slots"][0]["slot_id"]
    result = book_appointment(profile, store, slot_id, patient_name="Sam Reed")
    assert result["booked"]
    assert store.slots[slot_id]["status"] == "booked"
    assert store.bookings[0]["patient_name"] == "Sam Reed"


def test_double_booking_is_refused_and_never_claims_success(
    profile: PracticeProfile, store: MemoryStore
) -> None:
    slot_id = find_appointment(profile, store, "Cleaning / recall")["slots"][0]["slot_id"]
    assert book_appointment(profile, store, slot_id)["booked"]

    second = book_appointment(profile, store, slot_id)
    assert second["booked"] is False
    assert second["reason_code"] == "slot_taken"
    assert "do not tell the caller they are booked" in second["speak_hint"].lower()
    assert len(store.bookings) == 1


def test_unknown_slot_is_refused(profile: PracticeProfile, store: MemoryStore) -> None:
    result = book_appointment(profile, store, "not-a-slot")
    assert result["booked"] is False
    assert result["reason_code"] == "unknown_slot"


def test_store_raises_on_taken_slot(store: MemoryStore) -> None:
    slot_id = next(iter(store.slots))
    store.slots[slot_id]["status"] = "booked"
    with pytest.raises(SlotUnavailable):
        store.book(slot_id, {})


def test_spoken_time_is_readable(profile: PracticeProfile, store: MemoryStore) -> None:
    spoken = find_appointment(profile, store, "Cleaning / recall")["slots"][0]["spoken_time"]
    assert " at " in spoken
    assert spoken.endswith(("AM", "PM"))


# ---------------------------------------------------------------------------
# answer_from_kb
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "question",
    [
        "What insurance do you take?",
        "What are your hours on Wednesday?",
        "Who are the dentists there?",
        "What should I bring to my first visit?",
    ],
)
def test_answers_in_scope_questions(profile: PracticeProfile, question: str) -> None:
    result = answer_from_kb(profile, question)
    assert result["found"], f"expected an answer for {question!r}"
    assert result["answer"]


@pytest.mark.parametrize(
    "question",
    [
        "Do you do LASIK eye surgery here?",
        "Can you tell me my account balance from last year?",
        "Do you board dogs overnight?",
        "Which orthodontist across town would you recommend?",
    ],
)
def test_declines_out_of_scope_questions(profile: PracticeProfile, question: str) -> None:
    result = answer_from_kb(profile, question)
    assert result["found"] is False
    assert "office will confirm" in result["speak_hint"].lower()


def test_empty_question_is_handled(profile: PracticeProfile) -> None:
    assert answer_from_kb(profile, "   ")["found"] is False
