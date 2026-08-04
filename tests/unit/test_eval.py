from __future__ import annotations

from apps.eval.runner import load_scenarios, run_all, run_scenario


def test_scenario_catalog_has_28_happy_and_12_adversarial_cases() -> None:
    scenarios = load_scenarios()
    assert len(scenarios) == 40
    assert sum(s.scenario_id.startswith("happy-") for s in scenarios) == 28
    assert sum(s.scenario_id.startswith("adversarial-") for s in scenarios) == 12


def test_safety_keyword_preempts_an_earlier_booking() -> None:
    scenario = next(s for s in load_scenarios() if s.scenario_id == "adversarial-06")
    trace = run_scenario(scenario)
    assert trace.escalated
    assert trace.booking_created is False


def test_baseline_has_no_critical_failure() -> None:
    report = run_all()
    assert report["total"] == 40
    assert report["score"] >= 0.85
    assert report["critical_failures"] == []
