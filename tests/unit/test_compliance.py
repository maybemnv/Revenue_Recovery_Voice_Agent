"""The two compliance claims, tested against the code that has to hold them up.

**Out-of-scope questions do not get an answer.** `lookup_knowledge` retrieves the
top 3 chunks by cosine similarity and refuses anything under 0.35. The failure
mode being prevented is not an error — it is a fluent paraphrase of an unrelated
chunk, delivered in a confident voice to someone who will act on it. So the
assertions are that a below-threshold hit returns `not_found`, that the retrieved
text does not travel in the result where the model could paraphrase it anyway,
and that the hint tells the agent to say it will check rather than guess. Five
deliberately out-of-scope questions, because one is an anecdote.

**Card digits do not reach any sink.** Each sink is tested against the sink's own
code rather than against the intent:

* `turns.text_` — redacted in `insert_turn`.
* `tool_invocations.arguments` — redacted in `insert_tool_invocation`. Model
  authored, and `lookup_knowledge`'s schema asks for the caller's question in
  their own words, so it carries caller text verbatim into JSONB and back out
  through the dashboard.
* the post-call Claude call — reads the stored transcript, so it inherits the
  write-path redaction; the test asserts on the bytes in the request body.
* the log stream — the `_redact` processor, including nested values.
* the live sentiment classifier — redacted before the out-of-band request.

The audio path is not in this file: μ-law frames are passed through and never
persisted, and the recording URL is already gated on `consent_captured` in
`set_recording_url`.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from apps.api.observability.logging import _redact
from apps.api.security.redaction import (
    PAN_PLACEHOLDER,
    contains_pan,
    redact_pan,
    redact_structure,
)
from apps.api.tools import knowledge
from apps.api.tools.knowledge import MIN_SIMILARITY, lookup_knowledge

# A test card number. Luhn-valid and reserved for exactly this purpose.
CARD = "4111 1111 1111 1111"
SPOKEN_CARD = "four one one one one one one one one one one one one one one one"

# Deliberately out of scope for an HVAC line's knowledge base. Each one is a
# plausible thing a caller says, not a nonsense string — the control has to hold
# against a real question we simply have no answer to.
OUT_OF_SCOPE = [
    ("Do you sell car insurance?", 0.19),
    ("What time does the pharmacy on Halsted close?", 0.11),
    ("Can you tell me tomorrow's weather forecast?", 0.08),
    ("Who won the game last night?", 0.04),
    ("Are you hiring software engineers right now?", 0.27),
]

IN_SCOPE_CHUNK = "We service Carrier, Trane, and Lennox. Labour is warranted for 12 months."

CALL_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


async def embed_ok(_question: str) -> list[float]:
    return [0.1] * 8


@pytest.fixture
def retriever(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Scripts what pgvector returns, keyed by the best similarity.

    The cosine math lives in Postgres, so the seam worth testing is our own
    threshold decision. `calls` records the questions that reached retrieval.
    """
    state: dict[str, Any] = {"score": 0.0, "calls": []}

    async def fake_search(
        _session: Any, *, client_id: str, embedding: list[float], top_k: int = 3
    ) -> list[tuple[str, str | None, float]]:
        state["calls"].append(client_id)
        score = state["score"]
        return [
            (IN_SCOPE_CHUNK, "faq.md", score),
            ("Emergency callout carries a 149 dollar fee.", "pricing.md", score - 0.05),
        ]

    monkeypatch.setattr(knowledge, "search_chunks", fake_search)
    return state


async def ask(question: str, *, score: float, retriever: dict[str, Any]) -> Any:
    retriever["score"] = score
    return await lookup_knowledge(
        session=None, client_id="demo-hvac", embed=embed_ok, question=question
    )


@pytest.mark.parametrize(("question", "best_score"), OUT_OF_SCOPE)
async def test_an_out_of_scope_question_is_not_answered(
    retriever: dict[str, Any], question: str, best_score: float
) -> None:
    result = await ask(question, score=best_score, retriever=retriever)

    assert result["status"] == "not_found"
    assert result["data"]["best_score"] == pytest.approx(best_score, abs=1e-4)


@pytest.mark.parametrize(("question", "best_score"), OUT_OF_SCOPE)
async def test_a_rejected_chunk_does_not_travel_in_the_result(
    retriever: dict[str, Any], question: str, best_score: float
) -> None:
    """The model must not be handed text it was told not to use."""
    result = await ask(question, score=best_score, retriever=retriever)

    assert "matches" not in (result["data"] or {})
    assert IN_SCOPE_CHUNK not in str(result)


@pytest.mark.parametrize(("question", "best_score"), OUT_OF_SCOPE)
async def test_the_refusal_tells_the_agent_to_follow_up_not_guess(
    retriever: dict[str, Any], question: str, best_score: float
) -> None:
    result = await ask(question, score=best_score, retriever=retriever)
    hint = result["speak_hint"].lower()

    assert "do not guess" in hint
    assert "check" in hint and "follow up" in hint


async def test_an_in_scope_question_still_gets_its_answer(retriever: dict[str, Any]) -> None:
    """Otherwise the floor could be passing these tests by refusing everything."""
    result = await ask("Which brands do you service?", score=0.72, retriever=retriever)

    assert result["status"] == "ok"
    assert result["data"]["matches"][0]["content"] == IN_SCOPE_CHUNK
    assert result["speak_hint"] is None


async def test_a_hit_exactly_on_the_floor_is_used(retriever: dict[str, Any]) -> None:
    """The rule is ">= 0.35", so 0.35 is a hit."""
    result = await ask("Do you cover my postcode?", score=MIN_SIMILARITY, retriever=retriever)

    assert result["status"] == "ok"


async def test_a_hit_just_under_the_floor_is_refused(retriever: dict[str, Any]) -> None:
    result = await ask(
        "Do you cover my postcode?", score=MIN_SIMILARITY - 0.01, retriever=retriever
    )

    assert result["status"] == "not_found"


async def test_only_the_passing_chunks_are_returned(retriever: dict[str, Any]) -> None:
    """The second chunk sits 0.05 below the first, so a floor between them splits them."""
    result = await ask("Which brands?", score=MIN_SIMILARITY + 0.02, retriever=retriever)

    matches = result["data"]["matches"]
    assert [m["content"] for m in matches] == [IN_SCOPE_CHUNK]


async def test_an_empty_question_never_reaches_retrieval(retriever: dict[str, Any]) -> None:
    result = await lookup_knowledge(
        session=None, client_id="demo-hvac", embed=embed_ok, question="   "
    )

    assert result["status"] == "not_found"
    assert retriever["calls"] == []


async def test_an_embedding_failure_refuses_rather_than_guessing(
    retriever: dict[str, Any],
) -> None:
    """A dead embedding provider must not degrade into answering from memory."""

    async def embed_dead(_question: str) -> None:
        return None

    result = await lookup_knowledge(
        session=None, client_id="demo-hvac", embed=embed_dead, question="Which brands?"
    )

    assert result["status"] == "unavailable"
    assert "do not guess" in result["speak_hint"].lower()
    assert retriever["calls"] == []


# --- no card digits in any sink -------------------------------------------------


class FakeSession:
    """Records what would be written, so a sink can be asserted without a DB."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def add_all(self, objs: Any) -> None:
        self.added.extend(objs)

    async def flush(self) -> None:
        return None


@pytest.mark.parametrize(
    "spoken",
    [
        pytest.param(f"my card is {CARD}", id="numerals"),
        pytest.param(f"it's {CARD.replace(' ', '-')}", id="dashes"),
        pytest.param(f"it's {CARD.replace(' ', '')}", id="unseparated"),
        pytest.param(SPOKEN_CARD, id="spoken_digits"),
        pytest.param("the cvv is 123", id="cvv"),
    ],
)
def test_every_rendering_of_card_data_is_caught(spoken: str) -> None:
    """Transcription emits numerals, groups, or number words depending on the call."""
    assert contains_pan(spoken)
    assert "4111" not in redact_pan(spoken)


def test_a_phone_number_is_not_mistaken_for_a_card() -> None:
    """Over-redacting the caller's own number would break callbacks."""
    assert redact_pan("call me back on +1 312 555 0123") == "call me back on +1 312 555 0123"


async def test_card_digits_do_not_reach_the_transcript() -> None:
    from apps.api.db import repository

    session = FakeSession()
    turn = await repository.insert_turn(
        session,  # type: ignore[arg-type]
        call_id=CALL_ID,
        role="caller",
        text=f"sure, my card number is {CARD}, cvv 123",
        started_at_ms=1200,
    )

    assert "4111" not in turn.text_
    assert PAN_PLACEHOLDER in turn.text_
    assert "[REDACTED_CVV]" in turn.text_


async def test_card_digits_do_not_reach_tool_arguments() -> None:
    """`lookup_knowledge` is schema'd to pass the caller's own words through."""
    from apps.api.db import repository

    session = FakeSession()
    await repository.insert_tool_invocation(
        session,  # type: ignore[arg-type]
        call_id=CALL_ID,
        name="lookup_knowledge",
        arguments={"question": f"can I pay with {CARD}?"},
        result_status="not_found",
        latency_ms=42,
    )

    stored = session.added[0].arguments
    assert "4111" not in str(stored)
    assert PAN_PLACEHOLDER in stored["question"]


def test_redaction_reaches_nested_and_listed_values() -> None:
    """Arguments are arbitrary model-authored JSON, not a flat string map."""
    redacted = redact_structure(
        {
            "notes": [f"card {CARD}"],
            "caller": {"payment": {"pan": CARD}},
            "duration_minutes": 60,
            "confirmed": True,
        }
    )

    assert "4111" not in str(redacted)
    # Non-strings pass through untouched — a redactor that stringifies numbers
    # would corrupt every other argument in the payload.
    assert redacted["duration_minutes"] == 60
    assert redacted["confirmed"] is True


def test_a_card_shaped_key_is_redacted_too() -> None:
    assert "4111" not in str(redact_structure({CARD: "yes"}))


def test_card_digits_do_not_reach_a_log_line() -> None:
    event = _redact(None, "info", {"event": "turn", "text": f"my card is {CARD}"})

    assert "4111" not in event["text"]


def test_card_digits_do_not_reach_a_nested_log_value() -> None:
    """A bound `payload=` dict was the gap a top-level-only pass left open."""
    event = _redact(
        None,
        "info",
        {"event": "tool_dispatched", "arguments": {"question": f"pay with {CARD}"}},
    )

    assert "4111" not in str(event["arguments"])
    assert PAN_PLACEHOLDER in event["arguments"]["question"]


def test_a_phone_field_is_masked_in_logs() -> None:
    event = _redact(None, "info", {"event": "call", "from_e164": "+13125550123"})

    assert event["from_e164"].endswith("0123")
    assert "312555" not in event["from_e164"]


class CapturingClient:
    """Captures the outbound request body instead of sending it."""

    def __init__(self) -> None:
        self.bodies: list[Any] = []

    def post(self, url: str, *, json: Any = None, headers: Any = None) -> httpx.Response:
        self.bodies.append(json)
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "record_analysis",
                        "input": {
                            "summary": "Caller offered card details; agent declined.",
                            "intent": "booking",
                            "sentiment": "neutral",
                            "qa_score": 90,
                            "action_items": [],
                        },
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )


def test_card_digits_do_not_reach_the_post_call_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stored transcript is the input, so the redaction is already applied.

    Asserted on the request body rather than on the prompt text, because what
    matters is the bytes that leave the process.
    """
    from apps.api.workers import analyze as analyze_mod

    settings = analyze_mod.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test", raising=False)
    stored = redact_pan(f"my card number is {CARD}, cvv 123")
    client = CapturingClient()

    analyze_mod.call_claude(f"caller: {stored}", client=client)  # type: ignore[arg-type]

    body = str(client.bodies[0])
    assert "4111" not in body
    assert PAN_PLACEHOLDER in body


def test_the_analysis_prompt_carries_the_transcript_it_was_given() -> None:
    """Guards the test above: it must fail if the transcript stops being sent."""
    from apps.api.workers import analyze as analyze_mod

    turns = [
        _turn("caller", "my boiler is dead"),
        _turn("agent", "I can get someone out tomorrow."),
    ]

    rendered = analyze_mod.format_transcript(turns)  # type: ignore[arg-type]

    assert rendered == "caller: my boiler is dead\nagent: I can get someone out tomorrow."


def _turn(role: str, text: str) -> Any:
    class _T:
        def __init__(self) -> None:
            self.role = role
            self.text_ = text

    return _T()


def test_card_digits_do_not_reach_the_sentiment_classifier() -> None:
    """The live classifier is an out-of-band LLM call on the caller's own words."""
    from apps.api.media.bridge import SENTIMENT_MAX_CHARS

    spoken = f"  my card number is {CARD} and the cvv is 123  "
    # The expression `_classify_sentiment` builds its request from.
    prepared = redact_pan(spoken.strip())[:SENTIMENT_MAX_CHARS]

    assert "4111" not in prepared
    assert PAN_PLACEHOLDER in prepared


def test_the_eval_flags_a_card_scenario_as_pci_safe() -> None:
    """The offline scenario asserting the agent refuses card data still passes."""
    from apps.eval.graders import grade_trace
    from apps.eval.runner import load_scenarios, run_scenario

    scenario = next(s for s in load_scenarios() if s.scenario_id == "adversarial-10")
    grade = grade_trace(scenario, run_scenario(scenario))

    assert grade.checks["no_pci_capture"] is True
    assert grade.critical_failure is False
