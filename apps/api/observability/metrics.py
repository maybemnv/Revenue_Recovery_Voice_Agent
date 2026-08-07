"""Percentiles and gate verdicts for the numbers Gate 2 is decided on.

Every input already exists in the database. `turns.latency_ms` is written per
agent turn by the bridge, `call_events` carries the barge-in payload,
`tool_invocations` carries latency and status, `calls.cost_cents` carries spend.
Nothing here measures anything new — it aggregates what the media plane already
recorded and says whether the result clears the thresholds in `task.md:226-230`.

**Nearest-rank, not interpolated.** `percentile` returns a value that was
actually observed. The gate is verified across ~10 turns, and on a sample that
small an interpolating percentile reports a latency no caller experienced —
`p95` of ten turns becomes a blend of the two slowest rather than the slow one.
Nearest-rank inclusive is also exactly Postgres `percentile_disc`, so the SQL in
`routers/dashboard.py` and this module cannot disagree. `percentile_cont` is the
one to avoid: it is the default people reach for and it interpolates.

**A missing sample is not a zero.** No turns means `p50 is None`, not 0 ms, and a
`None` never fails a gate. A gate that passes because no data arrived is the one
failure mode that would make this whole module worse than reading the logs.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# From `task.md:226-230`. These are the Gate 2 ceilings, in milliseconds.
VOICE_P50_MS = 800
VOICE_P95_MS = 1400
BARGE_IN_CUTOFF_MS = 200
# Truncation accuracy. Measured as the audio handed to Twilio but not yet acked
# at the instant of the cut: the ledger derives `audio_end_ms` from acked marks
# only, so that unplayed remainder is the whole of the error between what the
# model is told the caller heard and what the caller actually heard.
TRUNCATION_TOLERANCE_MS = 100


def percentile(samples: Sequence[float], p: float) -> float | None:
    """Nearest-rank inclusive percentile. `p` is a fraction, so 0.95 not 95.

    Matches Postgres `percentile_disc(p)`: the smallest observed value whose
    cumulative share reaches `p`. Returns None for an empty sample rather than
    0.0, because "nothing was measured" and "it was instant" are opposite facts.
    """
    if not samples:
        return None
    ordered = sorted(samples)
    if p <= 0:
        return ordered[0]
    rank = math.ceil(p * len(ordered))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(frozen=True, slots=True)
class Distribution:
    """A latency distribution, plus the count it was computed from.

    `count` is reported alongside every percentile on purpose. A p95 over three
    samples is not a p95, and the reader needs to see that without going back to
    the query.
    """

    count: int
    p50: float | None = None
    p95: float | None = None
    max: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "p50_ms": _round(self.p50),
            "p95_ms": _round(self.p95),
            "max_ms": _round(self.max),
        }


def distribution(samples: Sequence[float]) -> Distribution:
    values = [float(s) for s in samples if s is not None]
    if not values:
        return Distribution(count=0)
    return Distribution(
        count=len(values),
        p50=percentile(values, 0.5),
        p95=percentile(values, 0.95),
        max=max(values),
    )


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 1)


@dataclass(frozen=True, slots=True)
class GateFailure:
    """One breached threshold, in terms a runbook can act on."""

    metric: str
    observed: float
    threshold: float

    def __str__(self) -> str:
        return f"{self.metric}: {self.observed:.0f}ms > {self.threshold:.0f}ms"


def _breach(metric: str, observed: float | None, threshold: float) -> GateFailure | None:
    """A breach needs an observation. An absent measurement never fails."""
    if observed is None or observed <= threshold:
        return None
    return GateFailure(metric=metric, observed=observed, threshold=threshold)


@dataclass(frozen=True, slots=True)
class ToolStats:
    """Per-tool latency and reliability. `failure_rate` excludes `not_found`.

    A `not_found` is the tool working correctly on a caller who asked about an
    address we do not serve. Counting it as a failure would make the service-area
    check look broken precisely when it is doing its job, so only `unavailable`
    and `denied` — a dead provider and a refused call — count against the rate.
    """

    name: str
    calls: int
    failures: int
    retried: int
    latency: Distribution

    @property
    def failure_rate(self) -> float:
        return round(self.failures / self.calls, 4) if self.calls else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "calls": self.calls,
            "failures": self.failures,
            "retried": self.retried,
            "failure_rate": self.failure_rate,
            "latency": self.latency.to_json(),
        }


FAILURE_STATUSES = frozenset({"unavailable", "denied"})


@dataclass(frozen=True, slots=True)
class CallMetrics:
    """Everything Gate 2 asks about, and the verdict.

    `passed` is derived from `failures`, never stored, so a report cannot be
    constructed that claims a pass while listing breaches.
    """

    voice_to_voice: Distribution
    barge_in_cutoff: Distribution
    truncation_error: Distribution
    tools: list[ToolStats]
    calls: int
    cost_usd: float
    failures: list[GateFailure]

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def cost_per_call_usd(self) -> float | None:
        return round(self.cost_usd / self.calls, 4) if self.calls else None

    def to_json(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "calls": self.calls,
            "cost_usd": round(self.cost_usd, 2),
            "cost_per_call_usd": self.cost_per_call_usd,
            "voice_to_voice": self.voice_to_voice.to_json(),
            "barge_in_cutoff": self.barge_in_cutoff.to_json(),
            "truncation_error": self.truncation_error.to_json(),
            "tools": [tool.to_json() for tool in self.tools],
            "failures": [str(failure) for failure in self.failures],
        }


def compute(
    *,
    turn_latencies_ms: Sequence[float],
    barge_in_cutoffs_ms: Sequence[float],
    truncation_errors_ms: Sequence[float],
    tool_rows: Sequence[tuple[str, str, float, int]],
    calls: int = 0,
    cost_cents: int = 0,
) -> CallMetrics:
    """Aggregate raw samples into the gate report.

    `tool_rows` is `(name, status, latency_ms, attempt)` — the shape of a
    `tool_invocations` row, so the caller can pass query output straight through
    without building an intermediate type.

    Thresholds are checked against p95, except the voice p50, which has its own
    ceiling. Barge-in and truncation are checked at p95 rather than the max: one
    carrier hiccup on one turn is not a regression in the cut-off path, and a
    gate that fails on a single outlier gets waived until it means nothing.
    """
    voice = distribution(turn_latencies_ms)
    cutoff = distribution(barge_in_cutoffs_ms)
    truncation = distribution(truncation_errors_ms)

    grouped: dict[str, list[tuple[str, float, int]]] = {}
    for name, status, latency_ms, attempt in tool_rows:
        grouped.setdefault(name, []).append((status, float(latency_ms), attempt))

    tools = [
        ToolStats(
            name=name,
            calls=len(rows),
            failures=sum(status in FAILURE_STATUSES for status, _, _ in rows),
            retried=sum(attempt > 1 for _, _, attempt in rows),
            latency=distribution([latency for _, latency, _ in rows]),
        )
        for name, rows in sorted(grouped.items())
    ]

    failures = [
        failure
        for failure in (
            _breach("voice_to_voice_p50", voice.p50, VOICE_P50_MS),
            _breach("voice_to_voice_p95", voice.p95, VOICE_P95_MS),
            _breach("barge_in_cutoff_p95", cutoff.p95, BARGE_IN_CUTOFF_MS),
            _breach("truncation_error_p95", truncation.p95, TRUNCATION_TOLERANCE_MS),
        )
        if failure is not None
    ]

    return CallMetrics(
        voice_to_voice=voice,
        barge_in_cutoff=cutoff,
        truncation_error=truncation,
        tools=tools,
        calls=calls,
        cost_usd=cost_cents / 100,
        failures=failures,
    )
