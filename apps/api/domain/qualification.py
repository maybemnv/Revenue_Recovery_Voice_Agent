"""Book, escalate, or decline — decided from state, not from model narration.

The out-of-area branch is the one worth reading twice. `capture_and_refer` is
the default because hanging up on a wrong-postcode caller throws away a lead
that is worth something to a partner business, and because a caller who gets
hung up on leaves a review about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.api.config.schema import ClientConfig
from apps.api.domain.escalation import should_escalate
from apps.api.domain.hours import DispatchDecision, dispatch_decision
from apps.api.domain.state import CallState


class QualificationAction(StrEnum):
    BOOK = "book"
    BOOK_EMERGENCY = "book_emergency"
    CAPTURE_AND_REFER = "capture_and_refer"
    ESCALATE = "escalate"
    DECLINE = "decline"
    CALLBACK = "callback"


@dataclass(frozen=True, slots=True)
class Qualification:
    action: QualificationAction
    reason: str

    @property
    def books(self) -> bool:
        return self.action in (QualificationAction.BOOK, QualificationAction.BOOK_EMERGENCY)


def qualify(state: CallState, cfg: ClientConfig, now: datetime) -> Qualification:
    # Escalation outranks qualification entirely: there is no version of "in the
    # service area" that makes a gas smell a booking.
    escalation = should_escalate(state, cfg)
    if escalation is not None:
        return Qualification(QualificationAction.ESCALATE, escalation.detail)

    if state.in_service_area is False:
        action = {
            "capture_and_refer": QualificationAction.CAPTURE_AND_REFER,
            "decline": QualificationAction.DECLINE,
            "transfer": QualificationAction.ESCALATE,
        }[cfg.service_area.out_of_area_action]
        return Qualification(action, f"postcode {state.postcode!r} outside service area")

    if state.in_service_area is None:
        return Qualification(QualificationAction.CALLBACK, "service area not yet established")

    decision = dispatch_decision(cfg, now, is_emergency=state.is_emergency)
    if decision is DispatchDecision.OPEN:
        return Qualification(QualificationAction.BOOK, "in area, business open")
    if decision is DispatchDecision.EMERGENCY_ONLY:
        return Qualification(QualificationAction.BOOK_EMERGENCY, "in area, emergency dispatch")
    return Qualification(QualificationAction.CALLBACK, "closed, non-emergency")
