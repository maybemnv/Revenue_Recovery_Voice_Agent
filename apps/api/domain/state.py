"""Per-call state. A plain dataclass with no I/O, so `domain/` stays pure.

The media plane mutates this; `domain/escalation.py` and `domain/qualification.py`
only read it. That split is what lets the escalation predicate be unit-tested
exhaustively without a socket, a database, or a clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class ToolOutcome:
    name: str
    status: str
    latency_ms: int
    attempt: int = 1

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(slots=True)
class CallState:
    call_id: str
    client_id: str
    from_e164: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Transcript-derived
    last_caller_text: str = ""
    caller_turns: int = 0
    agent_turns: int = 0

    # Escalation inputs
    human_requested: bool = False
    consecutive_tool_failures: int = 0
    negative_sentiment_turns: int = 0

    # Booking / qualification inputs
    postcode: str | None = None
    in_service_area: bool | None = None
    is_emergency: bool = False
    booking_confirmed: bool = False
    booking_in_flight: bool = False

    # Bookkeeping
    tool_history: list[ToolOutcome] = field(default_factory=list)
    escalated: bool = False
    consent_captured: bool = False

    def record_tool(self, outcome: ToolOutcome) -> None:
        self.tool_history.append(outcome)
        # A single success clears the streak; three *consecutive* failures is the
        # trigger, not three failures over the life of the call.
        self.consecutive_tool_failures = 0 if outcome.ok else self.consecutive_tool_failures + 1

    def record_sentiment(self, label: str) -> None:
        if label.lower() in {"negative", "frustrated", "angry", "hostile"}:
            self.negative_sentiment_turns += 1
        else:
            self.negative_sentiment_turns = 0

    def elapsed_ms(self, now: datetime | None = None) -> int:
        return int(((now or datetime.now(UTC)) - self.started_at).total_seconds() * 1000)
