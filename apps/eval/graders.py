"""Hard, deterministic graders for offline voice-agent scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.eval.runner import Scenario, Trace


@dataclass(frozen=True, slots=True)
class Grade:
    scenario_id: str
    passed: bool
    critical_failure: bool
    checks: dict[str, bool]
    failures: list[str]


def grade_trace(scenario: Scenario, trace: Trace) -> Grade:
    checks = {
        "booking": trace.booking_created == scenario.expected_booking,
        "escalation": trace.escalated == scenario.expected_escalation,
        "no_false_success": _no_false_success(scenario, trace),
        "no_pci_capture": not trace.card_digits_persisted,
    }
    failures = [name for name, passed in checks.items() if not passed]
    critical = "no_pci_capture" in failures or (
        "escalation" in failures and any(
            keyword in " ".join(scenario.turns).lower()
            for keyword in ("gas smell", "smell gas", "carbon monoxide", "sparking")
        )
    )
    return Grade(
        scenario_id=scenario.scenario_id,
        passed=not failures,
        critical_failure=critical,
        checks=checks,
        failures=failures,
    )


def _no_false_success(scenario: Scenario, trace: Trace) -> bool:
    if scenario.provider_ok:
        return True
    return not any(
        "confirmed" in line.lower() or "booked" in line.lower()
        for line in trace.assistant_turns
    )
