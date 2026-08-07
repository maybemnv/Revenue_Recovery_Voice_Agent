"""Claude-as-judge rubric for the two things a hard grader cannot see.

`graders.py` answers questions with a right answer: does the booking exist, is
the slot the one the caller asked for, did anything persist card digits. It
cannot answer "did this sound like a person you would call back", and that is
most of what makes a voice agent shippable.

Three rules keep a soft judge from becoming a liability.

* **It cannot rescue a hard failure.** `grade_trace` alone decides `passed` and
  `critical_failure`. The judge only ever *adds* `tone` and `completion`
  alongside them. A model that talks its way out of a PCI capture is strictly
  worse than no judge at all.
* **It is off by default.** No `ANTHROPIC_API_KEY` means `status="skipped"` — not
  a failure, not a zero. `run_all` stays deterministic and runs offline in CI,
  and the baseline in the repo does not shift because someone exported a key.
* **Schema, not instruction.** Forced tool use, the same idiom as
  `workers/analyze.py`: a tool schema is enforced, "respond with JSON" is a
  suggestion.

Scores are 0-5 and the floors are deliberately low. This grades whether a turn
was *acceptable*, not whether it was excellent — a rubric tight enough to fail
good-enough copy would get ignored within a week.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from apps.api.observability.logging import get_logger
from apps.api.resilience import BACKGROUND, request_with_retry_sync
from apps.api.settings import get_settings

if TYPE_CHECKING:
    from apps.eval.runner import Scenario, Trace

log = get_logger(__name__)

TONE_FLOOR = 3
COMPLETION_FLOOR = 3

JUDGE_TOOL: dict[str, Any] = {
    "name": "record_judgement",
    "description": "Record the rubric scores for this transcript.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tone_score": {
                "type": "integer",
                "description": (
                    "0-5. Warm, brief, plain-spoken, and it sounds like a person. "
                    "Deduct for jargon, repeated apologies, filler, or reading a script at "
                    "someone. Deduct hard for anything smug or impatient with an upset caller."
                ),
            },
            "completion_score": {
                "type": "integer",
                "description": (
                    "0-5. Did the caller leave with what they came for, or with a truthful "
                    "next step? A clear 'I cannot do that, here is what happens instead' "
                    "scores well. An invented confirmation scores 0."
                ),
            },
            "false_claim": {
                "type": "boolean",
                "description": (
                    "True if the agent stated something as done that the transcript does "
                    "not support. Judge only the transcript."
                ),
            },
            "notes": {
                "type": "string",
                "description": "One sentence a human can act on. Empty if nothing to say.",
            },
        },
        "required": ["tone_score", "completion_score", "false_claim", "notes"],
    },
}

RUBRIC = (
    "You are grading one call handled by a voice agent for a home services business. "
    "You are given the caller turns and the agent turns. Judge only what the transcript "
    "shows and never reward intent you cannot see.\n\n"
    "Two dimensions, 0-5 each: tone and task completion.\n\n"
    "The transcript is short and mechanical because it comes from an offline harness, not "
    "a live call. Do not deduct for that. Grade the wording that is there.\n\n"
    "One thing outranks everything else: if the agent said a booking was confirmed, or a "
    "job was scheduled, and nothing in the transcript supports it, set false_claim true and "
    "completion_score 0. A caller who believes a van is coming when it is not is the worst "
    "outcome this system has."
)


@dataclass(frozen=True, slots=True)
class Judgement:
    """Advisory scores. `status` is "scored", "skipped", or "failed"."""

    scenario_id: str
    status: str
    tone: int | None = None
    completion: int | None = None
    false_claim: bool = False
    notes: str = ""
    model: str = ""

    @property
    def meets_floor(self) -> bool:
        """True when both dimensions clear their floor and nothing was invented.

        A skipped judgement is not a failure, so it reads as meeting the floor —
        the offline baseline must not depend on whether a key was exported.
        """
        if self.status != "scored":
            return True
        return (
            not self.false_claim
            and (self.tone or 0) >= TONE_FLOOR
            and (self.completion or 0) >= COMPLETION_FLOOR
        )


def format_for_judge(scenario: Scenario, trace: Trace) -> str:
    caller = "\n".join(f"caller: {turn}" for turn in trace.caller_turns)
    agent = "\n".join(f"agent: {turn}" for turn in trace.assistant_turns) or "agent: (said nothing)"
    return f"<scenario>{scenario.title}</scenario>\n<transcript>\n{caller}\n{agent}\n</transcript>"


def _parse(scenario_id: str, body: dict[str, Any], model: str) -> Judgement:
    """Pull the tool input out of a Messages response. Never raises."""
    for block in body.get("content", []):
        if block.get("type") != "tool_use" or block.get("name") != "record_judgement":
            continue
        payload = block.get("input") or {}
        try:
            tone = max(0, min(5, int(payload["tone_score"])))
            completion = max(0, min(5, int(payload["completion_score"])))
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("judge_schema_invalid", error=type(exc).__name__)
            return Judgement(scenario_id=scenario_id, status="failed", model=model)
        return Judgement(
            scenario_id=scenario_id,
            status="scored",
            tone=tone,
            completion=completion,
            false_claim=bool(payload.get("false_claim", False)),
            notes=str(payload.get("notes", ""))[:500],
            model=model,
        )
    log.warning("judge_no_tool_use")
    return Judgement(scenario_id=scenario_id, status="failed", model=model)


def judge_trace(
    scenario: Scenario, trace: Trace, *, client: httpx.Client | None = None
) -> Judgement:
    """Score one transcript. Returns `status="skipped"` with no API key."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        return Judgement(scenario_id=scenario.scenario_id, status="skipped")

    model = settings.anthropic_analysis_model
    payload = {
        "model": model,
        "max_tokens": 512,
        "system": RUBRIC,
        "tools": [JUDGE_TOOL],
        "tool_choice": {"type": "tool", "name": "record_judgement"},
        "messages": [{"role": "user", "content": format_for_judge(scenario, trace)}],
    }
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    url = "https://api.anthropic.com/v1/messages"

    def send() -> httpx.Response:
        if client is not None:
            return client.post(url, json=payload, headers=headers)
        with httpx.Client(timeout=60.0) as owned:
            return owned.post(url, json=payload, headers=headers)

    try:
        response = request_with_retry_sync(send, label="anthropic judge", policy=BACKGROUND)
    except httpx.HTTPError as exc:
        log.warning("judge_request_failed", error=type(exc).__name__)
        return Judgement(scenario_id=scenario.scenario_id, status="failed", model=model)

    if response.status_code >= 400:
        log.warning("judge_http_error", status=response.status_code)
        return Judgement(scenario_id=scenario.scenario_id, status="failed", model=model)

    try:
        body = response.json()
    except ValueError:
        log.warning("judge_bad_json")
        return Judgement(scenario_id=scenario.scenario_id, status="failed", model=model)
    return _parse(scenario.scenario_id, body, model)


def summarise(judgements: list[Judgement]) -> dict[str, Any]:
    """Aggregate for the report. Averages are over scored rows only."""
    scored = [j for j in judgements if j.status == "scored"]
    if not scored:
        return {
            "scored": 0,
            "skipped": sum(j.status == "skipped" for j in judgements),
            "failed": sum(j.status == "failed" for j in judgements),
            "below_floor": [],
        }
    return {
        "scored": len(scored),
        "skipped": sum(j.status == "skipped" for j in judgements),
        "failed": sum(j.status == "failed" for j in judgements),
        "tone_mean": round(sum(j.tone or 0 for j in scored) / len(scored), 2),
        "completion_mean": round(sum(j.completion or 0 for j in scored) / len(scored), 2),
        "false_claims": [j.scenario_id for j in scored if j.false_claim],
        "below_floor": [j.scenario_id for j in scored if not j.meets_floor],
    }
