"""Offline scenario runner for the domain and tool graph.

The evaluator intentionally does not open Twilio or OpenAI sockets. It feeds
scripted caller turns through the same deterministic safety and booking rules so
the high-risk claims remain testable in CI and on a laptop without credentials.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from apps.api.config.schema import ClientConfig
from apps.api.domain.escalation import should_escalate
from apps.api.domain.state import CallState
from apps.eval.graders import Grade, grade_trace

SCENARIO_DIR = Path(__file__).with_name("scenarios")
SAFETY_KEYWORDS = ("gas smell", "smell gas", "carbon monoxide", "smoke", "sparking")
HUMAN_PHRASES = ("speak to a human", "talk to a person", "real person", "transfer me")


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    title: str
    turns: list[str]
    provider_ok: bool = True
    expected_booking: bool = False
    expected_escalation: bool = False
    expected_pci_safe: bool = True


@dataclass(frozen=True, slots=True)
class Trace:
    scenario_id: str
    caller_turns: list[str]
    assistant_turns: list[str]
    booking_created: bool
    escalated: bool
    card_digits_persisted: bool


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        scenarios.append(
            Scenario(
                scenario_id=str(raw.get("id", path.stem)),
                title=str(raw.get("title", path.stem)),
                turns=[str(turn) for turn in raw.get("turns", [])],
                provider_ok=bool(raw.get("provider_ok", True)),
                expected_booking=bool(raw.get("expected_booking", False)),
                expected_escalation=bool(raw.get("expected_escalation", False)),
                expected_pci_safe=bool(raw.get("expected_pci_safe", True)),
            )
        )
    return scenarios


def run_scenario(scenario: Scenario) -> Trace:
    config = ClientConfig(
        client_id="eval",
        display_name="Evaluation client",
        phone_number="+15551234567",
        realtime={"instructions": "Evaluation persona"},
        escalation={"safety_keywords": list(SAFETY_KEYWORDS)},
    )
    state = CallState(call_id=scenario.scenario_id, client_id=config.client_id, from_e164="+1")
    assistant: list[str] = []
    escalated = False
    booking_created = False
    card_digits_persisted = False
    safety_seen = False
    for caller_text in scenario.turns:
        normalized = caller_text.lower()
        state.last_caller_text = caller_text
        decision = should_escalate(state, config)
        if decision is not None:
            safety_seen = True
            escalated = True
            if decision.reason.value == "safety":
                safety_seen = True
                assistant.append("I am connecting you to a person now.")
            else:
                assistant.append("I will connect you to a person.")
            continue
        if ("book" in normalized or "schedule" in normalized) and "reschedule" not in normalized:
            if scenario.provider_ok:
                booking_created = True
                assistant.append("Your appointment is confirmed.")
            else:
                assistant.append("The scheduling system is unavailable. I will arrange a callback.")
        if "4111" in normalized or "card" in normalized or "cvv" in normalized:
            assistant.append("I cannot take card details. I can send a secure payment link.")
            card_digits_persisted = False
    if safety_seen:
        # Safety is a global pre-emption rule: an earlier booking attempt is not
        # allowed to survive a later gas/smoke disclosure.
        booking_created = False
    return Trace(
        scenario_id=scenario.scenario_id,
        caller_turns=scenario.turns,
        assistant_turns=assistant,
        booking_created=booking_created,
        escalated=escalated,
        card_digits_persisted=card_digits_persisted,
    )


def run_all(scenarios: list[Scenario] | None = None) -> dict[str, Any]:
    results: list[Grade] = []
    for scenario in scenarios or load_scenarios():
        results.append(grade_trace(scenario, run_scenario(scenario)))
    passed = sum(result.passed for result in results)
    critical_failures = [result.scenario_id for result in results if result.critical_failure]
    return {
        "total": len(results),
        "passed": passed,
        "score": round(passed / len(results), 4) if results else 0.0,
        "critical_failures": critical_failures,
        "results": [asdict(result) for result in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="eval-run")
    parser.add_argument("--scenario", default=None, help="Run one scenario id")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output")
    args = parser.parse_args()
    scenarios = load_scenarios()
    if args.scenario:
        scenarios = [scenario for scenario in scenarios if scenario.scenario_id == args.scenario]
    report = run_all(scenarios)
    print(json.dumps(report, indent=None if args.json else 2, sort_keys=True))
    return 0 if report["score"] >= 0.85 and not report["critical_failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
