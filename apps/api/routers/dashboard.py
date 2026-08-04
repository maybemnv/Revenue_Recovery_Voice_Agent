"""Read-only REST for the dashboard: call list, call detail, metrics.

Two tokens, two capabilities. `dashboard_api_token` can read everything and edit
configs; `dashboard_viewer_token` can only read. Both are optional and, when
unset, auth is skipped entirely — that is the local-dev path, and it is logged
at startup so it cannot be mistaken for a configured state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config.loader import ClientConfigNotFound, get_registry
from apps.api.db.models import Call, CallAnalysis, CallEvent, ToolInvocation, Turn
from apps.api.db.session import get_session
from apps.api.routers.auth import require_viewer
from apps.api.security.redaction import mask_e164

router = APIRouter(prefix="/api", tags=["dashboard"], dependencies=[Depends(require_viewer)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _call_summary(call: Call) -> dict[str, Any]:
    duration = (
        int((call.ended_at - call.started_at).total_seconds()) if call.ended_at else None
    )
    return {
        "id": str(call.id),
        "client_id": call.client_id,
        "from_e164": mask_e164(call.from_e164),
        "direction": call.direction,
        "started_at": call.started_at.isoformat(),
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "duration_seconds": duration,
        "outcome": call.outcome,
        "cost_cents": call.cost_cents,
        "has_recording": bool(call.recording_url),
    }


@router.get("/clients")
async def list_clients() -> list[dict[str, Any]]:
    return [
        {
            "client_id": cfg.client_id,
            "display_name": cfg.display_name,
            "phone_number": mask_e164(cfg.phone_number),
            "timezone": cfg.timezone,
            "tools_enabled": cfg.tools_enabled,
        }
        for cfg in get_registry().all()
    ]


@router.get("/clients/{client_id}/config")
async def get_client_config(client_id: str) -> dict[str, Any]:
    try:
        cfg = get_registry().get(client_id)
    except ClientConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return cfg.model_dump(mode="json")


@router.get("/calls")
async def list_calls(
    session: SessionDep,
    client_id: Annotated[str | None, Query()] = None,
    outcome: Annotated[str | None, Query()] = None,
    started_after: Annotated[date | None, Query()] = None,
    started_before: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    stmt = select(Call).order_by(Call.started_at.desc()).limit(limit).offset(offset)
    count_stmt = select(func.count()).select_from(Call)
    if client_id:
        stmt = stmt.where(Call.client_id == client_id)
        count_stmt = count_stmt.where(Call.client_id == client_id)
    if outcome:
        stmt = stmt.where(Call.outcome == outcome)
        count_stmt = count_stmt.where(Call.outcome == outcome)
    if started_after:
        boundary = datetime.combine(started_after, time.min, tzinfo=UTC)
        stmt = stmt.where(Call.started_at >= boundary)
        count_stmt = count_stmt.where(Call.started_at >= boundary)
    if started_before:
        boundary = datetime.combine(started_before, time.max, tzinfo=UTC)
        stmt = stmt.where(Call.started_at <= boundary)
        count_stmt = count_stmt.where(Call.started_at <= boundary)

    calls = list(await session.scalars(stmt))
    total = await session.scalar(count_stmt) or 0
    return {
        "items": [_call_summary(c) for c in calls],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/calls/{call_id}")
async def get_call_detail(call_id: uuid.UUID, session: SessionDep) -> dict[str, Any]:
    call = await session.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="call not found")

    turns = list(
        await session.scalars(
            select(Turn).where(Turn.call_id == call_id).order_by(Turn.started_at_ms)
        )
    )
    events = list(
        await session.scalars(
            select(CallEvent).where(CallEvent.call_id == call_id).order_by(CallEvent.at_ms)
        )
    )
    invocations = list(
        await session.scalars(
            select(ToolInvocation).where(ToolInvocation.call_id == call_id).order_by(
                ToolInvocation.id
            )
        )
    )
    analysis = await session.get(CallAnalysis, call_id)

    return {
        **_call_summary(call),
        "recording_url": call.recording_url,
        "turns": [
            {
                "role": t.role,
                "text": t.text_,
                "at_ms": t.started_at_ms,
                "latency_ms": t.latency_ms,
                "truncated_at_ms": t.truncated_at_ms,
            }
            for t in turns
        ],
        "events": [
            {"at_ms": e.at_ms, "kind": e.kind, "payload": e.payload} for e in events
        ],
        "tool_invocations": [
            {
                "name": i.name,
                "status": i.result_status,
                "latency_ms": i.latency_ms,
                "attempt": i.attempt,
                "arguments": i.arguments,
            }
            for i in invocations
        ],
        "analysis": (
            {
                "summary": analysis.summary,
                "intent": analysis.intent,
                "sentiment": analysis.sentiment,
                "qa_score": analysis.qa_score,
                "action_items": analysis.action_items,
                "model": analysis.model,
            }
            if analysis
            else None
        ),
    }


@router.get("/metrics")
async def metrics(
    session: SessionDep,
    client_id: Annotated[str | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> dict[str, Any]:
    """The four numbers on the dashboard header, computed in one round trip."""
    since = datetime.now(UTC) - timedelta(days=days)
    base = select(Call).where(Call.started_at >= since)
    if client_id:
        base = base.where(Call.client_id == client_id)
    subq = base.subquery()

    row = (
        await session.execute(
            select(
                func.count().label("total"),
                func.count().filter(subq.c.outcome == "booked").label("booked"),
                func.count().filter(subq.c.outcome == "escalated").label("escalated"),
                func.coalesce(func.sum(subq.c.cost_cents), 0).label("cost_cents"),
                func.avg(
                    func.extract("epoch", subq.c.ended_at - subq.c.started_at)
                ).label("avg_seconds"),
            ).select_from(subq)
        )
    ).one()

    total = row.total or 0
    p50_latency = await session.scalar(
        select(func.percentile_cont(0.5).within_group(Turn.latency_ms))
        .select_from(Turn)
        .join(Call, Call.id == Turn.call_id)
        .where(Turn.latency_ms.isnot(None), Call.started_at >= since)
    )

    return {
        "window_days": days,
        "total_calls": total,
        "booked": row.booked or 0,
        "escalated": row.escalated or 0,
        "booking_rate": round((row.booked or 0) / total, 4) if total else 0.0,
        "cost_usd": round((row.cost_cents or 0) / 100, 2),
        "avg_duration_seconds": round(float(row.avg_seconds), 1) if row.avg_seconds else None,
        "p50_response_latency_ms": int(p50_latency) if p50_latency is not None else None,
    }
