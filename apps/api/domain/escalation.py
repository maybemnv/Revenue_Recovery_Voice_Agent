"""Escalation is a deterministic predicate, never a model judgment.

Order is the whole design. Safety keywords are tested first and return
immediately, which is what makes a gas-smell call pre-empt an in-flight booking
rather than queue behind it. The match is a plain string test on the streaming
caller transcript, so it fires regardless of what the model was about to say.

Model judgment is the fallback, not the mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from apps.api.config.schema import ClientConfig
from apps.api.domain.state import CallState

# Phrasings that mean "get me a person" reliably enough to act on without a
# model in the loop. Kept narrow on purpose: "can you help me" is not a request
# for a human, and treating it as one would escalate most of the call volume.
HUMAN_REQUEST_PHRASES = (
    "speak to a human",
    "speak to a person",
    "talk to a human",
    "talk to a person",
    "talk to someone",
    "speak to someone",
    "real person",
    "real human",
    "get me a person",
    "transfer me",
    "put me through",
    "operator",
    "representative",
    "manager",
)


class EscalationReason(StrEnum):
    SAFETY = "safety"
    CALLER_REQUEST = "caller_request"
    TOOL_FAILURE = "tool_failure"
    FRUSTRATION = "frustration"


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    reason: EscalationReason
    detail: str

    @property
    def is_immediate(self) -> bool:
        """Safety transfers cut the conversation off; the rest wind it down."""
        return self.reason is EscalationReason.SAFETY


def matched_safety_keyword(text: str, cfg: ClientConfig) -> str | None:
    haystack = text.lower()
    for keyword in cfg.escalation.safety_keywords:
        if keyword in haystack:
            return keyword
    return None


def requests_human(text: str) -> bool:
    haystack = text.lower()
    return any(phrase in haystack for phrase in HUMAN_REQUEST_PHRASES)


def should_escalate(state: CallState, cfg: ClientConfig) -> EscalationDecision | None:
    esc = cfg.escalation

    # Highest priority, pre-empts everything — including a booking mid-flight.
    keyword = matched_safety_keyword(state.last_caller_text, cfg)
    if keyword is not None:
        return EscalationDecision(EscalationReason.SAFETY, f"safety keyword: {keyword}")

    if state.human_requested or requests_human(state.last_caller_text):
        return EscalationDecision(EscalationReason.CALLER_REQUEST, "caller asked for a human")

    if state.consecutive_tool_failures >= esc.max_consecutive_tool_failures:
        return EscalationDecision(
            EscalationReason.TOOL_FAILURE,
            f"{state.consecutive_tool_failures} consecutive tool failures",
        )

    if state.negative_sentiment_turns >= esc.max_negative_sentiment_turns:
        return EscalationDecision(
            EscalationReason.FRUSTRATION,
            f"{state.negative_sentiment_turns} negative turns",
        )

    return None
