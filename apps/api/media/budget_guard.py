"""Wall-clock and cost ceilings for a single call.

A voice agent that never hangs up is an unbounded bill. Two ceilings, both from
the client YAML: `max_call_seconds` and `max_call_cost_usd`. At
`wrap_up_at_pct` of whichever binds first the agent is told to start closing;
past 100% the call is ended by us rather than by the caller's patience.

Cost is estimated from measured per-minute rates rather than read from a meter,
because no meter exists mid-call. The estimate only has to be good enough to
stop a runaway — being 15% off on a call that should have ended four minutes ago
does not change the decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from apps.api.config.schema import BudgetConfig

# Measured against the demo config: Realtime audio in+out dominates, Twilio's
# per-minute is a rounding error next to it, and the post-call analysis is a
# fixed cost charged once.
REALTIME_USD_PER_MINUTE = 0.36
TWILIO_USD_PER_MINUTE = 0.0085
ANALYSIS_USD_FIXED = 0.02


class BudgetPhase(StrEnum):
    NORMAL = "normal"
    WRAP_UP = "wrap_up"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    phase: BudgetPhase
    elapsed_seconds: float
    estimated_cost_usd: float
    seconds_pct: float
    cost_pct: float
    binding: str

    @property
    def should_wrap_up(self) -> bool:
        return self.phase is not BudgetPhase.NORMAL

    @property
    def should_end(self) -> bool:
        return self.phase is BudgetPhase.EXPIRED


WRAP_UP_INSTRUCTION = (
    "You are near the end of the time available for this call. Bring it to a close in the "
    "next two turns: confirm what has been agreed, say you will follow up with the details, "
    "and thank the caller. Do not start anything new."
)


def estimate_cost_usd(elapsed_seconds: float) -> float:
    minutes = elapsed_seconds / 60.0
    return minutes * (REALTIME_USD_PER_MINUTE + TWILIO_USD_PER_MINUTE) + ANALYSIS_USD_FIXED


class BudgetGuard:
    """Pure and clock-free: `elapsed_seconds` is always supplied by the caller.

    The bridge owns the clock, so this stays testable by passing a number.
    """

    def __init__(self, budget: BudgetConfig) -> None:
        self._budget = budget
        self._wrapped_up = False

    @property
    def wrap_up_sent(self) -> bool:
        return self._wrapped_up

    def mark_wrap_up_sent(self) -> None:
        """Called once the instruction has actually been delivered to the model."""
        self._wrapped_up = True

    def check(self, elapsed_seconds: float) -> BudgetStatus:
        cost = estimate_cost_usd(elapsed_seconds)
        seconds_pct = 100.0 * elapsed_seconds / self._budget.max_call_seconds
        cost_pct = 100.0 * cost / self._budget.max_call_cost_usd

        # Whichever ceiling is closest is the one that governs.
        binding = "seconds" if seconds_pct >= cost_pct else "cost"
        worst = max(seconds_pct, cost_pct)

        if worst >= 100.0:
            phase = BudgetPhase.EXPIRED
        elif worst >= self._budget.wrap_up_at_pct:
            phase = BudgetPhase.WRAP_UP
        else:
            phase = BudgetPhase.NORMAL

        return BudgetStatus(
            phase=phase,
            elapsed_seconds=elapsed_seconds,
            estimated_cost_usd=round(cost, 4),
            seconds_pct=round(seconds_pct, 2),
            cost_pct=round(cost_pct, 2),
            binding=binding,
        )

    def needs_wrap_up_now(self, elapsed_seconds: float) -> BudgetStatus | None:
        """The wrap-up instruction is sent once, not on every tick."""
        status = self.check(elapsed_seconds)
        if status.phase is BudgetPhase.WRAP_UP and not self._wrapped_up:
            return status
        return None
