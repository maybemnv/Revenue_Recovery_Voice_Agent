from __future__ import annotations

from datetime import UTC, datetime

from apps.api.domain.escalation import EscalationReason, should_escalate
from apps.api.domain.hours import DispatchDecision, dispatch_decision, is_open, next_open
from apps.api.domain.qualification import QualificationAction, qualify
from apps.api.domain.state import CallState, ToolOutcome


def _moment(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 3, hour, minute, tzinfo=UTC)


def test_hours_use_business_timezone_and_half_open_boundaries(config) -> None:
    assert is_open(config, _moment(13, 0)) is True  # 08:00 Chicago
    assert is_open(config, _moment(23, 0)) is False  # 18:00 Chicago
    assert next_open(config, _moment(14, 0)).hour == 9
    assert next_open(config, _moment(23, 0)).hour == 8


def test_emergency_dispatch_only_applies_after_hours(config) -> None:
    assert dispatch_decision(config, _moment(13), is_emergency=False) is DispatchDecision.OPEN
    assert (
        dispatch_decision(config, _moment(23), is_emergency=True)
        is DispatchDecision.EMERGENCY_ONLY
    )

    config.hours.emergency_dispatch = "never"
    assert dispatch_decision(config, _moment(23), is_emergency=True) is DispatchDecision.CLOSED


def test_state_tracks_consecutive_failures_and_resets_on_success() -> None:
    state = CallState(call_id="c", client_id="demo", from_e164="+13125551111")
    state.record_tool(ToolOutcome("availability", "unavailable", 1200))
    state.record_tool(ToolOutcome("booking", "not_found", 2000))
    assert state.consecutive_tool_failures == 2
    state.record_tool(ToolOutcome("service_area", "ok", 10))
    assert state.consecutive_tool_failures == 0
    state.record_sentiment("angry")
    state.record_sentiment("neutral")
    assert state.negative_sentiment_turns == 0


def test_escalation_precedence_covers_all_triggers(config) -> None:
    state = CallState(call_id="c", client_id=config.client_id, from_e164="+1")
    state.last_caller_text = "I smell gas"
    state.booking_in_flight = True
    state.human_requested = True
    state.consecutive_tool_failures = 3
    state.negative_sentiment_turns = 2
    assert should_escalate(state, config).reason is EscalationReason.SAFETY

    state.last_caller_text = "Please put me through to a person"
    state.human_requested = False
    state.consecutive_tool_failures = 0
    state.negative_sentiment_turns = 0
    assert should_escalate(state, config).reason is EscalationReason.CALLER_REQUEST

    state.last_caller_text = ""
    state.consecutive_tool_failures = 3
    assert should_escalate(state, config).reason is EscalationReason.TOOL_FAILURE
    state.consecutive_tool_failures = 0
    state.negative_sentiment_turns = 2
    assert should_escalate(state, config).reason is EscalationReason.FRUSTRATION


def test_qualification_routes_area_hours_and_emergency(config) -> None:
    state = CallState(call_id="c", client_id=config.client_id, from_e164="+1")
    assert qualify(state, config, _moment(13)).action is QualificationAction.CALLBACK

    state.in_service_area = False
    assert qualify(state, config, _moment(13)).action is QualificationAction.CAPTURE_AND_REFER

    state.in_service_area = True
    assert qualify(state, config, _moment(13)).action is QualificationAction.BOOK
    assert qualify(state, config, _moment(23)).action is QualificationAction.CALLBACK

    state.is_emergency = True
    assert qualify(state, config, _moment(23)).action is QualificationAction.BOOK_EMERGENCY

    state.last_caller_text = "gas leak"
    assert qualify(state, config, _moment(13)).action is QualificationAction.ESCALATE


def test_qualification_respects_decline_and_transfer_area_policies(config) -> None:
    state = CallState(call_id="c", client_id=config.client_id, from_e164="+1")
    state.in_service_area = False
    config.service_area.out_of_area_action = "decline"
    assert qualify(state, config, _moment(13)).action is QualificationAction.DECLINE
    config.service_area.out_of_area_action = "transfer"
    assert qualify(state, config, _moment(13)).action is QualificationAction.ESCALATE
