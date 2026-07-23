"""The four mock tools.

Identical across every clone. They read the prospect profile and the demo
calendar, and nothing else — no PMS, no eligibility API, no LLM in the hot path.
Every one returns in well under 300 ms so the agent never leaves dead air.

The load-bearing rule lives in `check_insurance`: it reports the practice's
network status and never the caller's coverage. A demo agent that says "yes,
you're covered" is a demo agent that loses the deal on the first objection.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any
from zoneinfo import ZoneInfo

from clone.kb_builder import render_knowledge_base
from clone.profile import PracticeProfile
from webhooks.store import CalendarStore, SlotUnavailable

VERIFY_DISCLAIMER = (
    "The office verifies each patient's individual benefits before the visit — "
    "network status is not the same as coverage."
)

# Carriers get abbreviated, misheard, and half-said on a phone line. Normalising
# before matching is what keeps "Delta" and "Delta Dental PPO" from reading as
# two different carriers, and what keeps a real carrier from falling through to
# `unknown` because of a transcription artifact.
_CARRIER_NOISE = re.compile(
    r"\b(dental|insurance|ppo|hmo|dmo|plan|coverage|the|my|i have|we have)\b", re.I
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def normalize_carrier(raw: str) -> str:
    text = _NON_ALNUM.sub(" ", raw.lower())
    text = _CARRIER_NOISE.sub(" ", text)
    return " ".join(text.split())


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def check_insurance(profile: PracticeProfile, carrier: str) -> dict[str, Any]:
    """`accepted | out_of_network | unknown`, plus a never-confirm disclaimer."""
    said = normalize_carrier(carrier)
    if not said:
        return {
            "status": "unknown",
            "carrier": carrier,
            "in_network": False,
            "disclaimer": VERIFY_DISCLAIMER,
            "speak_hint": "Ask the caller which insurance carrier they have.",
        }

    if not profile.insurance_accepted:
        # No published carrier list means we cannot claim any carrier is accepted.
        return {
            "status": "unknown",
            "carrier": carrier,
            "in_network": False,
            "disclaimer": VERIFY_DISCLAIMER,
            "speak_hint": (
                "Say the office confirms which plans they are in-network with, offer to "
                "book anyway, and do not name any carrier as accepted or not accepted."
            ),
        }

    for accepted in profile.insurance_accepted:
        known = normalize_carrier(accepted)
        if not known:
            continue
        hit = known == said or known in said or said in known or _similar(known, said) >= 0.85
        if hit:
            return {
                "status": "accepted",
                "carrier": accepted,
                "in_network": True,
                "disclaimer": VERIFY_DISCLAIMER,
                "speak_hint": (
                    f"Confirm the practice is in-network with {accepted}, then say the "
                    "office verifies their specific benefits before the visit. Do not "
                    "tell the caller they are covered. Then move to booking."
                ),
            }

    return {
        "status": "out_of_network",
        "carrier": carrier,
        "in_network": False,
        "accepted_carriers": profile.insurance_accepted,
        "notes": profile.insurance_notes,
        "disclaimer": VERIFY_DISCLAIMER,
        "speak_hint": (
            f"Say the practice is not in-network with {carrier}, name a couple of the "
            "carriers they are in-network with, and offer to book anyway — the front "
            "desk goes through out-of-network claims and payment options."
        ),
    }


def _role_for(profile: PracticeProfile, appointment_type: str) -> tuple[str, str]:
    """Resolve a spoken appointment type to (canonical name, provider role)."""
    said = appointment_type.lower().strip()
    best: tuple[float, str, str] | None = None
    for kind in profile.appointment_types:
        score = max(
            _similar(kind.name.lower(), said),
            1.0 if said and said in kind.name.lower() else 0.0,
            1.0 if kind.name.lower() in said else 0.0,
        )
        if best is None or score > best[0]:
            best = (score, kind.name, kind.provider_role)
    if best is None:
        return appointment_type, "dentist"
    _, name, role = best
    return name, role


def _speak_time(slot: dict[str, Any], timezone: str) -> str:
    starts = datetime.fromisoformat(str(slot["starts_at"]).replace("Z", "+00:00"))
    if starts.tzinfo is None:
        starts = starts.replace(tzinfo=UTC)
    local = starts.astimezone(ZoneInfo(timezone))
    hour = local.strftime("%I").lstrip("0") or "12"
    minute = "" if local.minute == 0 else f":{local.minute:02d}"
    meridiem = local.strftime("%p").replace("AM", "AM").replace("PM", "PM")
    return f"{local.strftime('%A')} the {local.day} at {hour}{minute} {meridiem}"


def find_appointment(
    profile: PracticeProfile,
    store: CalendarStore,
    appointment_type: str,
    preference: str | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    canonical, role = _role_for(profile, appointment_type)
    slots = store.find_slots(profile.prospect_id, role, limit=3, after=now)

    if not slots:
        return {
            "found": False,
            "appointment_type": canonical,
            "slots": [],
            "speak_hint": (
                "Say you do not have anything open for that visit type right now and "
                "offer to take their details so the office can call with an opening. "
                "Do not invent a time."
            ),
        }

    return {
        "found": True,
        "appointment_type": canonical,
        "preference_noted": preference,
        "slots": [
            {
                "slot_id": slot["id"],
                "provider": slot["provider_name"],
                "spoken_time": _speak_time(slot, profile.timezone),
                "starts_at": slot["starts_at"],
                "duration_minutes": slot.get("duration_minutes", 60),
            }
            for slot in slots
        ],
        "speak_hint": (
            "Offer these times in plain language, two at a time. Wait for the caller "
            "to choose before booking."
        ),
    }


def book_appointment(
    profile: PracticeProfile,
    store: CalendarStore,
    slot_id: str,
    *,
    patient_name: str | None = None,
    patient_phone: str | None = None,
    reason: str | None = None,
    appointment_type: str | None = None,
) -> dict[str, Any]:
    slot = store.get_slot(slot_id)
    if slot is None:
        return {
            "booked": False,
            "reason_code": "unknown_slot",
            "speak_hint": (
                "Say you lost that time while confirming, then offer to look again. "
                "Do not tell the caller they are booked."
            ),
        }
    try:
        result = store.book(
            slot_id,
            {
                "prospect_id": profile.prospect_id,
                "patient_name": patient_name,
                "patient_phone": patient_phone,
                "reason": reason,
                "appointment_type": appointment_type,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
    except SlotUnavailable:
        return {
            "booked": False,
            "reason_code": "slot_taken",
            "speak_hint": (
                "Say that time was just taken and offer the other options you gave. "
                "Do not tell the caller they are booked."
            ),
        }

    spoken = _speak_time(result, profile.timezone)
    return {
        "booked": True,
        "slot_id": slot_id,
        "provider": result["provider_name"],
        "spoken_time": spoken,
        "confirmation": f"{spoken} with {result['provider_name']}",
        "speak_hint": (
            f"Confirm: booked {spoken} with {result['provider_name']}. Then ask for a "
            "name and mobile number for the reminder if you do not have them yet."
        ),
    }


# ---------------------------------------------------------------------------
# answer_from_kb
# ---------------------------------------------------------------------------
_STOPWORDS = {
    "a", "an", "and", "any", "are", "at", "be", "can", "do", "does", "for", "from", "have",
    "how", "i", "in", "is", "it", "me", "my", "of", "on", "or", "the", "there", "they",
    "to", "we", "what", "when", "where", "who", "will", "with", "you", "your",
}
MIN_SCORE = 0.34


def _stem(word: str) -> str:
    """Crude singularisation. Callers say "dentists" and "hours"; the KB writes
    "dentist" and "Hours", and an exact-match retriever misses both."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]
    return word


def _tokens(text: str) -> set[str]:
    return {
        _stem(w)
        for w in re.findall(r"[a-z0-9]+", text.lower())
        if w not in _STOPWORDS and len(w) > 2
    }


def _sections(markdown: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    title, body = "Overview", []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if body:
                out.append((title, "\n".join(body).strip()))
            title, body = line[3:].strip(), []
        else:
            body.append(line)
    if body:
        out.append((title, "\n".join(body).strip()))
    return [(t, b) for t, b in out if b]


def answer_from_kb(profile: PracticeProfile, question: str) -> dict[str, Any]:
    """Grounded lookup over this practice's KB, or an explicit 'I'll check'.

    Deterministic scoring rather than a model call: the answer must be traceable
    to a KB section, and a retrieval step that cannot invent text is a stronger
    guarantee than a prompt asking a model not to. Below threshold it returns
    `found=false`, which the prompt turns into "the office will confirm".
    """
    asked = _tokens(question)
    if not asked:
        return {"found": False, "speak_hint": "Ask the caller to repeat the question."}

    best_title, best_body, best_score = "", "", 0.0
    for title, body in _sections(render_knowledge_base(profile)):
        overlap = asked & (_tokens(title) | _tokens(body))
        # Title matches count double: "Insurance" in the heading is a stronger
        # signal than the word appearing once in a paragraph of prose.
        score = (len(overlap) + len(asked & _tokens(title))) / (len(asked) + 1)
        if score > best_score:
            best_title, best_body, best_score = title, body, score

    if best_score < MIN_SCORE:
        return {
            "found": False,
            "question": question,
            "speak_hint": (
                "You do not have this in the practice information. Say the office will "
                "confirm and offer to have someone call them back. Do not answer anyway."
            ),
        }

    return {
        "found": True,
        "question": question,
        "section": best_title,
        "answer": best_body[:1200],
        "speak_hint": (
            "Answer using only what is in `answer`, in one or two spoken sentences. "
            "If it does not actually address the question, say the office will confirm."
        ),
    }
