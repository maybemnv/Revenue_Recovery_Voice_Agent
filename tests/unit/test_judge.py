"""The soft judge is advisory, opt-in, and cannot rescue a hard failure.

`graders.py` decides `passed` and `critical_failure`. The judge adds tone and
completion alongside them. These tests pin the three properties that keep it from
becoming a liability:

* **No key, no judge.** `run_all()` with no `--judge` makes zero HTTP calls, and
  a missing `ANTHROPIC_API_KEY` yields `status="skipped"` rather than a zero. The
  committed baseline must not move because someone exported a key.
* **Every provider misbehaviour is `status="failed"`, never a score.** A 500, a
  truncated body, a response with no `tool_use` block, a `tone_score` of
  `"four"` — all of them land on `failed`, and `failed` is not a low score.
* **The hard verdict is untouchable.** A judgement that invents a false claim and
  scores 0/0 leaves `score` and `critical_failures` exactly as they were.

`meets_floor` reads True for a skipped or failed judgement on purpose: the floor
is a claim about a transcript that was actually graded, and treating an absent
grade as a failure would make CI depend on credentials.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from apps.eval import judge as judge_mod
from apps.eval.judge import (
    COMPLETION_FLOOR,
    TONE_FLOOR,
    Judgement,
    format_for_judge,
    judge_trace,
    summarise,
)
from apps.eval.runner import Scenario, Trace, run_all, run_scenario

SCENARIO = Scenario(
    scenario_id="happy-11",
    title="Caller books a slot",
    turns=["I need to book a repair"],
    expected_booking=True,
)


def trace_for(scenario: Scenario = SCENARIO) -> Trace:
    return run_scenario(scenario)


def tool_use_body(**overrides: Any) -> dict[str, Any]:
    """A Messages response shaped like a forced `record_judgement` call."""
    payload: dict[str, Any] = {
        "tone_score": 4,
        "completion_score": 5,
        "false_claim": False,
        "notes": "Confirmed the slot without padding.",
    }
    payload.update(overrides)
    return {
        "content": [
            {"type": "text", "text": "ignored"},
            {"type": "tool_use", "name": "record_judgement", "input": payload},
        ]
    }


class FakeAnthropic:
    """Returns a scripted response and counts calls."""

    def __init__(self, status: int = 200, body: Any = None, *, text: str | None = None) -> None:
        self.status = status
        self.body = body if body is not None else tool_use_body()
        self.text = text
        self.calls = 0
        self.payloads: list[dict[str, Any]] = []

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        self.calls += 1
        self.payloads.append(json)
        request = httpx.Request("POST", url)
        if self.text is not None:
            return httpx.Response(self.status, text=self.text, request=request)
        return httpx.Response(self.status, json=self.body, request=request)


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key present, so `judge_trace` gets past its skip guard."""
    settings = judge_mod.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test", raising=False)


@pytest.fixture
def keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = judge_mod.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)


def test_no_api_key_skips_without_calling_the_provider(keyless: None) -> None:
    client = FakeAnthropic()

    judgement = judge_trace(SCENARIO, trace_for(), client=client)  # type: ignore[arg-type]

    assert judgement.status == "skipped"
    assert client.calls == 0
    # A skip is an absent grade, not a failing one.
    assert judgement.meets_floor is True


def test_a_wellformed_tool_use_is_scored(keyed: None) -> None:
    client = FakeAnthropic()

    judgement = judge_trace(SCENARIO, trace_for(), client=client)  # type: ignore[arg-type]

    assert judgement.status == "scored"
    assert (judgement.tone, judgement.completion) == (4, 5)
    assert judgement.false_claim is False
    assert judgement.notes == "Confirmed the slot without padding."
    assert judgement.meets_floor is True


def test_the_request_forces_the_judgement_tool(keyed: None) -> None:
    """Forced tool use, not a JSON instruction: the schema is the contract."""
    client = FakeAnthropic()

    judge_trace(SCENARIO, trace_for(), client=client)  # type: ignore[arg-type]

    payload = client.payloads[0]
    assert payload["tool_choice"] == {"type": "tool", "name": "record_judgement"}
    assert [tool["name"] for tool in payload["tools"]] == ["record_judgement"]
    assert payload["system"] == judge_mod.RUBRIC


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("tone_score", 9, 5),
        ("tone_score", -3, 0),
        ("completion_score", 42, 5),
    ],
)
def test_scores_are_clamped_to_the_rubric_range(
    keyed: None, field: str, value: int, expected: int
) -> None:
    """A model that returns 9/5 is wrong about the scale, not about the call."""
    client = FakeAnthropic(body=tool_use_body(**{field: value}))

    judgement = judge_trace(SCENARIO, trace_for(), client=client)  # type: ignore[arg-type]

    scored = judgement.tone if field == "tone_score" else judgement.completion
    assert scored == expected


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"content": [{"type": "text", "text": "sure thing"}]}, id="no_tool_use"),
        pytest.param(
            {"content": [{"type": "tool_use", "name": "something_else", "input": {}}]},
            id="wrong_tool",
        ),
        pytest.param({"content": []}, id="empty_content"),
        pytest.param({}, id="no_content_key"),
    ],
)
def test_a_response_without_the_tool_call_fails_rather_than_scoring(
    keyed: None, body: dict[str, Any]
) -> None:
    client = FakeAnthropic(body=body)

    judgement = judge_trace(SCENARIO, trace_for(), client=client)  # type: ignore[arg-type]

    assert judgement.status == "failed"
    assert judgement.tone is None and judgement.completion is None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"tone_score": "four"}, id="non_numeric"),
        pytest.param({"tone_score": None}, id="null"),
        pytest.param({"completion_score": [5]}, id="wrong_type"),
    ],
)
def test_a_malformed_score_fails_rather_than_defaulting_to_zero(
    keyed: None, payload: dict[str, Any]
) -> None:
    """Defaulting to 0 would look like a damning verdict on the agent."""
    client = FakeAnthropic(body=tool_use_body(**payload))

    judgement = judge_trace(SCENARIO, trace_for(), client=client)  # type: ignore[arg-type]

    assert judgement.status == "failed"


def test_a_missing_score_key_fails(keyed: None) -> None:
    body = {
        "content": [
            {"type": "tool_use", "name": "record_judgement", "input": {"tone_score": 4}},
        ]
    }
    client = FakeAnthropic(body=body)

    judgement = judge_trace(SCENARIO, trace_for(), client=client)  # type: ignore[arg-type]

    assert judgement.status == "failed"


def test_an_http_error_fails(keyed: None) -> None:
    client = FakeAnthropic(status=500, body={"error": "overloaded"})

    judgement = judge_trace(SCENARIO, trace_for(), client=client)  # type: ignore[arg-type]

    assert judgement.status == "failed"
    # BACKGROUND retries a 500 before giving up.
    assert client.calls > 1


def test_a_truncated_body_fails(keyed: None) -> None:
    client = FakeAnthropic(text="{not json")

    judgement = judge_trace(SCENARIO, trace_for(), client=client)  # type: ignore[arg-type]

    assert judgement.status == "failed"


def test_a_transport_error_fails_without_propagating(keyed: None) -> None:
    class Dead:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, url: str, **_: Any) -> httpx.Response:
            self.calls += 1
            raise httpx.ConnectError("no route", request=httpx.Request("POST", url))

    client = Dead()

    judgement = judge_trace(SCENARIO, trace_for(), client=client)  # type: ignore[arg-type]

    assert judgement.status == "failed"


def test_notes_are_truncated(keyed: None) -> None:
    client = FakeAnthropic(body=tool_use_body(notes="x" * 900))

    judgement = judge_trace(SCENARIO, trace_for(), client=client)  # type: ignore[arg-type]

    assert len(judgement.notes) == 500


def test_a_false_claim_misses_the_floor_at_any_score() -> None:
    """An invented confirmation is the failure mode the rubric exists for."""
    judgement = Judgement(
        scenario_id="happy-11",
        status="scored",
        tone=5,
        completion=5,
        false_claim=True,
    )

    assert judgement.meets_floor is False


@pytest.mark.parametrize(
    ("tone", "completion", "expected"),
    [
        (TONE_FLOOR, COMPLETION_FLOOR, True),
        (TONE_FLOOR - 1, COMPLETION_FLOOR, False),
        (TONE_FLOOR, COMPLETION_FLOOR - 1, False),
        (5, 5, True),
    ],
)
def test_both_dimensions_must_clear_their_floor(tone: int, completion: int, expected: bool) -> None:
    judgement = Judgement(scenario_id="s", status="scored", tone=tone, completion=completion)

    assert judgement.meets_floor is expected


def test_a_failed_judgement_does_not_read_as_below_floor() -> None:
    """A provider outage is not evidence about the agent."""
    assert Judgement(scenario_id="s", status="failed").meets_floor is True


def test_format_for_judge_labels_both_speakers() -> None:
    trace = Trace(
        scenario_id="s",
        caller_turns=["I need to book"],
        assistant_turns=["Your appointment is confirmed."],
        booking_created=True,
        escalated=False,
        card_digits_persisted=False,
    )

    rendered = format_for_judge(SCENARIO, trace)

    assert "caller: I need to book" in rendered
    assert "agent: Your appointment is confirmed." in rendered
    assert SCENARIO.title in rendered


def test_format_for_judge_marks_a_silent_agent() -> None:
    """An empty agent side must not render as an empty transcript."""
    trace = Trace(
        scenario_id="s",
        caller_turns=["hello?"],
        assistant_turns=[],
        booking_created=False,
        escalated=False,
        card_digits_persisted=False,
    )

    assert "agent: (said nothing)" in format_for_judge(SCENARIO, trace)


def test_summarise_averages_over_scored_rows_only() -> None:
    judgements = [
        Judgement(scenario_id="a", status="scored", tone=4, completion=5),
        Judgement(scenario_id="b", status="scored", tone=2, completion=3),
        Judgement(scenario_id="c", status="skipped"),
        Judgement(scenario_id="d", status="failed"),
    ]

    summary = summarise(judgements)

    assert (summary["scored"], summary["skipped"], summary["failed"]) == (2, 1, 1)
    assert summary["tone_mean"] == 3.0
    assert summary["completion_mean"] == 4.0
    # `b` misses the tone floor; the skipped and failed rows are not listed.
    assert summary["below_floor"] == ["b"]


def test_summarise_reports_false_claims_separately() -> None:
    judgements = [
        Judgement(scenario_id="a", status="scored", tone=5, completion=0, false_claim=True),
        Judgement(scenario_id="b", status="scored", tone=4, completion=4),
    ]

    summary = summarise(judgements)

    assert summary["false_claims"] == ["a"]
    assert summary["below_floor"] == ["a"]


def test_summarise_with_nothing_scored_reports_no_means() -> None:
    """No scored rows means no average, not an average of zero."""
    summary = summarise([Judgement(scenario_id="a", status="skipped")])

    assert summary == {"scored": 0, "skipped": 1, "failed": 0, "below_floor": []}


def test_summarise_of_an_empty_list_is_empty() -> None:
    assert summarise([])["scored"] == 0


def test_run_all_without_judge_makes_no_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default eval path stays deterministic and offline."""
    calls = 0

    def explode(*_: Any, **__: Any) -> Judgement:
        nonlocal calls
        calls += 1
        raise AssertionError("run_all must not judge unless asked")

    monkeypatch.setattr("apps.eval.runner.judge_trace", explode)

    report = run_all()

    assert calls == 0
    assert "judge" not in report and "judgements" not in report
    assert report["critical_failures"] == []


def test_run_all_with_judge_attaches_an_advisory_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def scored(scenario: Scenario, _trace: Trace) -> Judgement:
        return Judgement(scenario_id=scenario.scenario_id, status="scored", tone=4, completion=4)

    monkeypatch.setattr("apps.eval.runner.judge_trace", scored)

    report = run_all([SCENARIO], judge=True)

    assert report["judge"]["scored"] == 1
    assert report["judge"]["below_floor"] == []
    assert len(report["judgements"]) == 1
    assert report["judgements"][0]["scenario_id"] == "happy-11"


def test_a_damning_judgement_cannot_flip_the_hard_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole reason the judge is advisory. 0/0 plus a false claim, still a pass."""
    baseline = run_all()

    def damning(scenario: Scenario, _trace: Trace) -> Judgement:
        return Judgement(
            scenario_id=scenario.scenario_id,
            status="scored",
            tone=0,
            completion=0,
            false_claim=True,
        )

    monkeypatch.setattr("apps.eval.runner.judge_trace", damning)

    judged = run_all(judge=True)

    assert judged["score"] == baseline["score"]
    assert judged["critical_failures"] == baseline["critical_failures"]
    assert judged["passed"] == baseline["passed"]
    # The signal is still recorded — just not in the gate.
    assert len(judged["judge"]["below_floor"]) == judged["total"]


def test_a_judge_outage_cannot_fail_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = run_all()

    monkeypatch.setattr(
        "apps.eval.runner.judge_trace",
        lambda scenario, _trace: Judgement(scenario_id=scenario.scenario_id, status="failed"),
    )

    judged = run_all(judge=True)

    assert judged["score"] == baseline["score"]
    assert judged["judge"]["failed"] == judged["total"]
    assert judged["judge"]["below_floor"] == []
