"""Gate 2 arithmetic: nearest-rank percentiles, and no pass on absent data.

Two properties matter more than the rest.

* **Nearest-rank, not interpolated.** The gate is verified across ~10 turns.
  `percentile_cont` on ten samples reports a p95 that sits between the two
  slowest turns — a latency no caller experienced. Every percentile here is a
  value that was actually observed, which also makes it identical to Postgres
  `percentile_disc` so the SQL and the Python cannot drift apart.
* **A missing sample is not a zero.** No turns means `p50 is None` and the gate
  neither passes nor fails on it. The dangerous bug is the opposite: an empty
  table reading as 0 ms and reporting a green gate for a system nobody called.

The rest pins the tool rollup — `not_found` is a working service-area check, not
a failure — and that `passed` is derived from `failures` so no report can claim a
pass while listing breaches.
"""

from __future__ import annotations

import pytest

from apps.api.observability.metrics import (
    BARGE_IN_CUTOFF_MS,
    TRUNCATION_TOLERANCE_MS,
    VOICE_P50_MS,
    VOICE_P95_MS,
    CallMetrics,
    Distribution,
    ToolStats,
    compute,
    distribution,
    percentile,
)


def test_percentile_of_an_empty_sample_is_none() -> None:
    """Not 0.0. "Nothing was measured" and "it was instant" are opposite facts."""
    assert percentile([], 0.5) is None


@pytest.mark.parametrize(
    ("p", "expected"),
    [
        (0.5, 5),
        (0.95, 10),
        (1.0, 10),
        (0.0, 1),
        (0.1, 1),
    ],
)
def test_percentile_returns_an_observed_value(p: float, expected: float) -> None:
    """Ten samples, so an interpolating percentile would invent 9.5 at p95."""
    samples = list(range(1, 11))

    assert percentile(samples, p) == expected


def test_p95_of_ten_samples_is_the_slowest_not_a_blend() -> None:
    """The exact case the gate runs on: 9 fast turns and one slow one."""
    samples = [200.0] * 9 + [3000.0]

    assert percentile(samples, 0.95) == 3000.0
    assert percentile(samples, 0.5) == 200.0


def test_percentile_of_one_sample_is_that_sample() -> None:
    assert percentile([42.0], 0.5) == 42.0
    assert percentile([42.0], 0.95) == 42.0


def test_percentile_does_not_require_sorted_input() -> None:
    assert percentile([9.0, 1.0, 5.0], 0.5) == 5.0


def test_distribution_reports_the_count_alongside_the_percentiles() -> None:
    """A p95 over three samples is not a p95; the reader has to be able to see that."""
    dist = distribution([100.0, 200.0, 900.0])

    assert dist.count == 3
    assert dist.max == 900.0
    assert dist.to_json() == {
        "count": 3,
        "p50_ms": 200.0,
        "p95_ms": 900.0,
        "max_ms": 900.0,
    }


def test_an_empty_distribution_has_no_percentiles() -> None:
    dist = distribution([])

    assert dist.count == 0
    assert dist.to_json() == {"count": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}


def test_distribution_skips_missing_samples() -> None:
    """`turns.latency_ms` is nullable: a caller turn has no latency."""
    dist = distribution([100.0, None, 300.0])  # type: ignore[list-item]

    assert dist.count == 2
    # Nearest-rank over two samples takes the lower, exactly like `percentile_disc`.
    assert dist.p50 == 100.0
    assert dist.p95 == 300.0


def fast_gate(**overrides: object) -> CallMetrics:
    """A run that clears every threshold, so each test breaches exactly one."""
    kwargs: dict = {
        "turn_latencies_ms": [400.0, 500.0, 600.0],
        "barge_in_cutoffs_ms": [80.0, 120.0],
        "truncation_errors_ms": [20.0, 40.0],
        "tool_rows": [],
        "calls": 3,
        "cost_cents": 150,
    }
    kwargs.update(overrides)
    return compute(**kwargs)  # type: ignore[arg-type]


def test_a_fast_run_passes_with_no_failures() -> None:
    report = fast_gate()

    assert report.passed is True
    assert report.failures == []
    assert report.to_json()["failures"] == []


def test_an_empty_window_passes_without_claiming_measurements() -> None:
    """No data must not fail the gate — and must not report zeros either."""
    report = compute(
        turn_latencies_ms=[],
        barge_in_cutoffs_ms=[],
        truncation_errors_ms=[],
        tool_rows=[],
    )

    assert report.passed is True
    assert report.voice_to_voice.p50 is None
    assert report.voice_to_voice.count == 0
    assert report.cost_per_call_usd is None


def test_a_slow_p50_fails_the_gate() -> None:
    report = fast_gate(turn_latencies_ms=[VOICE_P50_MS + 1] * 3)

    assert report.passed is False
    assert [f.metric for f in report.failures] == ["voice_to_voice_p50"]


def test_a_slow_p95_fails_even_when_p50_is_fine() -> None:
    """The tail is the whole point: nine good turns do not excuse the tenth."""
    report = fast_gate(turn_latencies_ms=[300.0] * 9 + [float(VOICE_P95_MS + 500)])

    assert [f.metric for f in report.failures] == ["voice_to_voice_p95"]


def test_a_value_exactly_on_the_threshold_passes() -> None:
    """The gate reads "≤ 800 ms", so 800 is a pass."""
    report = fast_gate(
        turn_latencies_ms=[float(VOICE_P50_MS)],
        barge_in_cutoffs_ms=[float(BARGE_IN_CUTOFF_MS)],
        truncation_errors_ms=[float(TRUNCATION_TOLERANCE_MS)],
    )

    assert report.passed is True


def test_a_slow_barge_in_cutoff_fails() -> None:
    report = fast_gate(barge_in_cutoffs_ms=[float(BARGE_IN_CUTOFF_MS + 50)] * 4)

    assert [f.metric for f in report.failures] == ["barge_in_cutoff_p95"]


def test_truncation_drift_beyond_tolerance_fails() -> None:
    """Unplayed audio at the cut is the model believing the caller heard more."""
    report = fast_gate(truncation_errors_ms=[float(TRUNCATION_TOLERANCE_MS + 1)] * 4)

    assert [f.metric for f in report.failures] == ["truncation_error_p95"]


def test_every_breach_is_reported_not_just_the_first() -> None:
    report = fast_gate(
        turn_latencies_ms=[5000.0] * 4,
        barge_in_cutoffs_ms=[900.0] * 4,
        truncation_errors_ms=[900.0] * 4,
    )

    assert [f.metric for f in report.failures] == [
        "voice_to_voice_p50",
        "voice_to_voice_p95",
        "barge_in_cutoff_p95",
        "truncation_error_p95",
    ]


def test_a_failure_renders_the_observation_and_the_threshold() -> None:
    """The string goes in a runbook, so it has to say what was seen and expected."""
    report = fast_gate(turn_latencies_ms=[2500.0] * 3)

    assert str(report.failures[0]) == "voice_to_voice_p50: 2500ms > 800ms"


def test_passed_is_derived_from_failures() -> None:
    """No report can claim a pass while listing a breach."""
    breached = fast_gate(turn_latencies_ms=[9000.0] * 3)

    assert breached.failures
    assert breached.passed is False
    assert breached.to_json()["passed"] is False


TOOL_ROWS = [
    ("check_availability", "ok", 420.0, 1),
    ("check_availability", "unavailable", 1200.0, 2),
    ("check_availability", "ok", 380.0, 1),
    ("check_service_area", "not_found", 8.0, 1),
    ("book_appointment", "ok", 900.0, 1),
]


def test_tools_are_grouped_and_sorted_by_name() -> None:
    report = compute(
        turn_latencies_ms=[],
        barge_in_cutoffs_ms=[],
        truncation_errors_ms=[],
        tool_rows=TOOL_ROWS,
    )

    assert [t.name for t in report.tools] == [
        "book_appointment",
        "check_availability",
        "check_service_area",
    ]


def test_a_not_found_is_not_a_tool_failure() -> None:
    """An out-of-area postcode is the service-area check working."""
    report = compute(
        turn_latencies_ms=[],
        barge_in_cutoffs_ms=[],
        truncation_errors_ms=[],
        tool_rows=TOOL_ROWS,
    )
    area = next(t for t in report.tools if t.name == "check_service_area")

    assert (area.calls, area.failures) == (1, 0)
    assert area.failure_rate == 0.0


@pytest.mark.parametrize(
    ("status", "counts"),
    [("unavailable", 1), ("denied", 1), ("not_found", 0), ("ok", 0)],
)
def test_only_unavailable_and_denied_count_against_the_rate(status: str, counts: int) -> None:
    report = compute(
        turn_latencies_ms=[],
        barge_in_cutoffs_ms=[],
        truncation_errors_ms=[],
        tool_rows=[("book_appointment", status, 100.0, 1)],
    )

    assert report.tools[0].failures == counts


def test_tool_failure_rate_and_retries_are_per_tool() -> None:
    report = compute(
        turn_latencies_ms=[],
        barge_in_cutoffs_ms=[],
        truncation_errors_ms=[],
        tool_rows=TOOL_ROWS,
    )
    availability = next(t for t in report.tools if t.name == "check_availability")

    assert availability.calls == 3
    assert availability.failures == 1
    assert availability.retried == 1
    assert availability.failure_rate == round(1 / 3, 4)
    assert availability.latency.p95 == 1200.0


def test_a_tool_with_no_calls_has_a_zero_rate_not_a_division_error() -> None:
    assert (
        ToolStats(name="x", calls=0, failures=0, retried=0, latency=Distribution(0)).failure_rate
        == 0.0
    )


def test_tool_latency_is_measured_per_tool_not_pooled() -> None:
    """A 2s booking must not drag the 8ms service-area check's percentile up."""
    report = compute(
        turn_latencies_ms=[],
        barge_in_cutoffs_ms=[],
        truncation_errors_ms=[],
        tool_rows=TOOL_ROWS,
    )

    by_name = {t.name: t for t in report.tools}
    assert by_name["check_service_area"].latency.p95 == 8.0
    assert by_name["book_appointment"].latency.p95 == 900.0


def test_tool_stats_do_not_affect_the_gate_verdict() -> None:
    """Tool failures are reported, not gated: a dead provider is not a latency regression."""
    report = compute(
        turn_latencies_ms=[400.0],
        barge_in_cutoffs_ms=[],
        truncation_errors_ms=[],
        tool_rows=[("book_appointment", "unavailable", 5000.0, 3)],
    )

    assert report.passed is True
    assert report.tools[0].failure_rate == 1.0


def test_cost_per_call_divides_spend_by_calls() -> None:
    report = fast_gate(calls=4, cost_cents=1000)

    assert report.cost_usd == 10.0
    assert report.cost_per_call_usd == 2.5
    assert report.to_json()["cost_per_call_usd"] == 2.5


def test_cost_per_call_with_no_calls_is_none_not_zero() -> None:
    assert fast_gate(calls=0, cost_cents=0).cost_per_call_usd is None


def test_to_json_carries_every_gate_dimension() -> None:
    payload = fast_gate(tool_rows=TOOL_ROWS).to_json()

    assert set(payload) == {
        "passed",
        "calls",
        "cost_usd",
        "cost_per_call_usd",
        "voice_to_voice",
        "barge_in_cutoff",
        "truncation_error",
        "tools",
        "failures",
    }
    assert payload["voice_to_voice"]["p95_ms"] == 600.0
    assert len(payload["tools"]) == 3
