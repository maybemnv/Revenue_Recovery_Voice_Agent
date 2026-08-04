"""Post-call analysis with Claude: summary, intent, sentiment, QA score.

The prompt is given the transcript that is already in the database, which means
it is already PAN-redacted — the analysis model never sees card data because the
write path removed it before storage, not because the prompt asks nicely.

JSON is requested via a tool definition rather than "respond with JSON", because
a tool schema is enforced and an instruction is a suggestion. A response that
still fails to parse produces a `qa_score` of 0 and a summary saying analysis
failed, which is visible in the dashboard rather than silently absent.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from apps.api.db.models import Call, CallAnalysis, Turn
from apps.api.observability.logging import get_logger
from apps.api.settings import get_settings

log = get_logger(__name__)

ANALYSIS_TOOL: dict[str, Any] = {
    "name": "record_analysis",
    "description": "Record the structured analysis of this call.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Two sentences: what the caller wanted and what happened.",
            },
            "intent": {
                "type": "string",
                "enum": [
                    "booking",
                    "quote",
                    "emergency",
                    "reschedule",
                    "complaint",
                    "question",
                    "other",
                ],
            },
            "sentiment": {
                "type": "string",
                "enum": ["positive", "neutral", "negative"],
            },
            "qa_score": {
                "type": "integer",
                "description": (
                    "0-100. Did the agent qualify, avoid inventing facts, and follow the "
                    "escalation rules? Deduct heavily for any confirmed booking that the "
                    "tool results do not support."
                ),
            },
            "action_items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "What a human still needs to do. Empty if nothing.",
            },
        },
        "required": ["summary", "intent", "sentiment", "qa_score", "action_items"],
    },
}

SYSTEM_PROMPT = (
    "You are a QA analyst for a voice agent that answers calls for a home services business. "
    "You are given a transcript. Judge only what the transcript shows. If the agent claimed "
    "something was booked or confirmed and nothing in the transcript supports it, that is the "
    "most serious failure there is and the QA score must reflect it."
)

FAILED_ANALYSIS = {
    "summary": "Analysis failed — the transcript is stored but was not scored.",
    "intent": "other",
    "sentiment": "neutral",
    "qa_score": 0,
    "action_items": ["Review this call manually."],
}


class AnalysisPayload(BaseModel):
    """The only shape accepted from the post-call model."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=4000)
    intent: str
    sentiment: str
    qa_score: int = Field(ge=0, le=100)
    action_items: list[str] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Analysis:
    summary: str
    intent: str
    sentiment: str
    qa_score: int
    action_items: list[str]
    model: str


def format_transcript(turns: list[Turn]) -> str:
    return "\n".join(f"{t.role}: {t.text_}" for t in turns)


def call_claude(transcript: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """One Messages API call with a forced tool use. Never raises."""
    settings = get_settings()
    if not settings.anthropic_api_key or not transcript.strip():
        return dict(FAILED_ANALYSIS)

    payload = {
        "model": settings.anthropic_analysis_model,
        "max_tokens": 1024,
        "system": SYSTEM_PROMPT,
        "tools": [ANALYSIS_TOOL],
        "tool_choice": {"type": "tool", "name": "record_analysis"},
        "messages": [{"role": "user", "content": f"<transcript>\n{transcript}\n</transcript>"}],
    }
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        if client is not None:
            response = client.post(
                "https://api.anthropic.com/v1/messages", json=payload, headers=headers
            )
        else:
            with httpx.Client(timeout=60.0) as owned:
                response = owned.post(
                    "https://api.anthropic.com/v1/messages", json=payload, headers=headers
                )
    except httpx.HTTPError as exc:
        log.warning("analysis_request_failed", error=type(exc).__name__)
        return dict(FAILED_ANALYSIS)

    if response.status_code >= 400:
        log.warning("analysis_http_error", status=response.status_code)
        return dict(FAILED_ANALYSIS)

    for block in response.json().get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == "record_analysis":
            try:
                return AnalysisPayload.model_validate(block.get("input", {})).model_dump()
            except Exception as exc:  # malformed model output is a visible failure
                log.warning("analysis_schema_invalid", error=type(exc).__name__)
                return dict(FAILED_ANALYSIS)

    log.warning("analysis_no_tool_use")
    return dict(FAILED_ANALYSIS)


def analyze(
    session: Session, call_id: uuid.UUID, *, client: httpx.Client | None = None
) -> Analysis:
    call = session.get(Call, call_id)
    if call is None:
        raise LookupError(f"no call {call_id}")

    turns = list(
        session.scalars(
            select(Turn).where(Turn.call_id == call_id).order_by(Turn.started_at_ms)
        )
    )
    raw = call_claude(format_transcript(turns), client=client)
    settings = get_settings()

    analysis = Analysis(
        summary=str(raw.get("summary", ""))[:4000],
        intent=str(raw.get("intent", "other")),
        sentiment=str(raw.get("sentiment", "neutral")),
        qa_score=max(0, min(100, int(raw.get("qa_score", 0)))),
        action_items=[str(a) for a in raw.get("action_items", [])],
        model=settings.anthropic_analysis_model,
    )

    # Upsert, because `acks_late` means this task can legitimately run twice.
    stmt = pg_insert(CallAnalysis).values(
        call_id=call_id,
        summary=analysis.summary,
        intent=analysis.intent,
        sentiment=analysis.sentiment,
        qa_score=analysis.qa_score,
        action_items=analysis.action_items,
        model=analysis.model,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[CallAnalysis.call_id],
            set_={
                "summary": stmt.excluded.summary,
                "intent": stmt.excluded.intent,
                "sentiment": stmt.excluded.sentiment,
                "qa_score": stmt.excluded.qa_score,
                "action_items": stmt.excluded.action_items,
                "model": stmt.excluded.model,
            },
        )
    )
    log.info(
        "call_analysed",
        call_id=str(call_id),
        intent=analysis.intent,
        qa_score=analysis.qa_score,
        turns=len(turns),
    )
    return analysis


def analysis_to_json(analysis: Analysis) -> str:
    return json.dumps(
        {
            "summary": analysis.summary,
            "intent": analysis.intent,
            "sentiment": analysis.sentiment,
            "qa_score": analysis.qa_score,
            "action_items": analysis.action_items,
        }
    )
