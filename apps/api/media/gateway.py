"""The `/media` WebSocket endpoint: everything a call needs, assembled once.

Ordering matters at both ends. On the way up, the OpenAI socket is opened before
Twilio's first `media` frame can arrive, so no caller audio is dropped on the
floor waiting for a connection. On the way down, the DB row is finalised in a
`finally` — a call that crashed still has to appear in the dashboard, because a
call list that silently omits failures is worse than no call list at all.

Persistence is wired in here as hooks rather than called from inside the bridge,
which is what lets the bridge be tested against a fake socket and no database.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from apps.api.config.loader import ClientConfigNotFound, get_registry
from apps.api.db import repository
from apps.api.db.session import get_sessionmaker, session_scope
from apps.api.domain.escalation import EscalationDecision
from apps.api.domain.state import CallState
from apps.api.media.bridge import BridgeHooks, MediaBridge
from apps.api.media.realtime_client import RealtimeClient
from apps.api.observability.live import get_hub
from apps.api.observability.logging import bind_call_context, clear_call_context, get_logger
from apps.api.tools.factory import build_bundle
from apps.api.tools.registry import Invocation

log = get_logger(__name__)

router = APIRouter()

# Live calls, so a process shutdown can ask each one to wind down instead of
# dropping them mid-sentence. Guarded because the shutdown hook runs in the
# lifespan task while call handlers mutate it concurrently.
_active_bridges: set[MediaBridge] = set()
_bridges_lock = asyncio.Lock()


async def track_bridge(bridge: MediaBridge) -> None:
    async with _bridges_lock:
        _active_bridges.add(bridge)


async def untrack_bridge(bridge: MediaBridge) -> None:
    async with _bridges_lock:
        _active_bridges.discard(bridge)


async def shutdown_active_bridges() -> int:
    """Ask every live call to drain, flush, and close. Best-effort, bounded.

    Each bridge's own drain is capped at `DRAIN_TIMEOUT_SECONDS`, so this cannot
    hang the shutdown. The handler tasks themselves finish once their sockets
    close; uvicorn's `--timeout-graceful-shutdown` is what waits for them, which
    the deployment runbook should set.
    """
    async with _bridges_lock:
        bridges = list(_active_bridges)
    for bridge in bridges:
        with contextlib.suppress(Exception):
            await bridge.shutdown()
    if bridges:
        log.info("shutdown_signalled_active_calls", count=len(bridges))
    return len(bridges)


@router.websocket("/media/{call_id}")
async def media_stream(websocket: WebSocket, call_id: uuid.UUID) -> None:
    """Twilio connects here for the life of one call."""
    await _run_media_stream(websocket, call_id)


@router.websocket("/media")
async def legacy_media_stream(websocket: WebSocket) -> None:
    """Compatibility endpoint for streams created before path call IDs."""
    await _run_media_stream(websocket, None)


async def _run_media_stream(websocket: WebSocket, path_call_id: uuid.UUID | None) -> None:
    await websocket.accept()

    params = websocket.query_params
    client_id = params.get("client_id", "")
    call_sid = params.get("call_sid", "")
    from_e164 = params.get("from", "")
    consent = params.get("consent", "") == "1"

    try:
        config = get_registry().get(client_id)
    except ClientConfigNotFound:
        log.warning("media_unknown_client", client_id=client_id)
        await websocket.close(code=1008)
        return

    call_id = path_call_id or uuid.uuid4()
    bind_call_context(call_id=str(call_id), client_id=client_id, twilio_call_sid=call_sid)

    async with session_scope() as session:
        existing = await repository.get_call(session, call_id)
        if existing is None:
            if path_call_id is not None:
                log.warning("media_call_not_found", call_id=str(call_id))
                await websocket.close(code=1008)
                return
            await repository.create_call(
                session,
                call_id=call_id,
                client_id=client_id,
                twilio_call_sid=call_sid,
                from_e164=from_e164,
                consent_captured=consent,
            )
        else:
            client_id = existing.client_id
            call_sid = existing.twilio_call_sid
            from_e164 = existing.from_e164
            consent = existing.consent_captured
            bind_call_context(call_id=str(call_id), client_id=client_id, twilio_call_sid=call_sid)

    state = CallState(
        call_id=str(call_id),
        client_id=client_id,
        from_e164=from_e164,
        consent_captured=consent,
    )
    hooks = _build_hooks(call_id, state)
    outcome = "failed"
    barge_ins = 0
    tool_calls = 0

    try:
        realtime = RealtimeClient(model=config.realtime.model)
        # Connected before the first Twilio frame can land.
        async with realtime:
            bridge = MediaBridge(
                twilio=websocket,
                realtime=realtime,
                config=config,
                state=state,
                tools=build_bundle(
                    config,
                    session_factory=get_sessionmaker(),
                    call_id=str(call_id),
                    call_sid=call_sid,
                    from_e164=from_e164,
                ),
                hooks=hooks,
            )
            await track_bridge(bridge)
            try:
                stats = await bridge.run()
            finally:
                await untrack_bridge(bridge)
            barge_ins, tool_calls = stats.barge_ins, stats.tool_calls
        outcome = _classify(state)
    except WebSocketDisconnect:
        log.info("twilio_disconnected")
        outcome = _classify(state)
    except Exception:
        log.exception("media_session_failed")
    finally:
        async with session_scope() as session:
            await repository.finish_call(session, call_id, outcome=outcome)
        await get_hub().publish(str(call_id), "call_ended", {"outcome": outcome})
        log.info(
            "call_finished",
            outcome=outcome,
            caller_turns=state.caller_turns,
            agent_turns=state.agent_turns,
            barge_ins=barge_ins,
            tool_calls=tool_calls,
        )
        clear_call_context()
        _enqueue_post_call(call_id)


def _classify(state: CallState) -> str:
    """Map end-of-call state onto the `CALL_OUTCOMES` vocabulary."""
    if state.booking_confirmed:
        return "booked"
    if state.escalated:
        return "escalated"
    if state.in_service_area is False:
        return "out_of_area"
    if state.caller_turns == 0:
        return "abandoned"
    return "qualified"


def _build_hooks(call_id: uuid.UUID, state: CallState) -> BridgeHooks:
    hub = get_hub()

    async def on_event(kind: str, payload: dict[str, Any]) -> None:
        async with session_scope() as session:
            await repository.insert_call_event(
                session,
                call_id=call_id,
                at_ms=int(payload.get("at_ms", state.elapsed_ms())),
                kind=kind,
                payload=payload,
            )
        await hub.publish(str(call_id), kind, payload)

    async def on_turn(role: str, text: str, at_ms: int, meta: dict[str, Any]) -> None:
        async with session_scope() as session:
            turn = await repository.insert_turn(
                session,
                call_id=call_id,
                role=role,
                text=text,
                started_at_ms=at_ms,
                latency_ms=meta.get("latency_ms"),
                realtime_item_id=meta.get("item_id"),
            )
            # Publish what was *stored*, so the live feed inherits the same
            # redaction as the transcript rather than re-implementing it.
            stored = turn.text_
        await hub.publish(str(call_id), "turn", {"role": role, "text": stored, "at_ms": at_ms})

    async def on_invocation(invocation: Invocation) -> None:
        async with session_scope() as session:
            await repository.insert_tool_invocation(
                session,
                call_id=call_id,
                name=invocation.name,
                arguments=invocation.arguments,
                result_status=invocation.result["status"],
                latency_ms=invocation.latency_ms,
                attempt=invocation.attempts,
            )
        if invocation.name == "book_appointment" and invocation.result["status"] == "ok":
            state.booking_confirmed = True
        if invocation.name == "check_service_area" and invocation.result["status"] == "ok":
            data = invocation.result["data"] or {}
            state.in_service_area = data.get("in_area")
            state.postcode = data.get("postcode")

    async def on_truncation(item_id: str, audio_end_ms: int) -> None:
        async with session_scope() as session:
            await repository.set_turn_truncation(
                session,
                call_id=call_id,
                realtime_item_id=item_id,
                truncated_at_ms=audio_end_ms,
            )

    async def on_escalation(decision: EscalationDecision) -> None:
        log.info("escalation_recorded", reason=str(decision.reason), detail=decision.detail)

    return BridgeHooks(
        on_event=on_event,
        on_turn=on_turn,
        on_truncation=on_truncation,
        on_invocation=on_invocation,
        on_escalation=on_escalation,
    )


def _enqueue_post_call(call_id: uuid.UUID) -> None:
    """Hand off to Celery. A broker that is down must not fail the call."""
    try:
        from apps.api.workers.tasks import analyze_call

        analyze_call.delay(str(call_id))
    except Exception as exc:  # the call has already ended
        log.warning("post_call_enqueue_failed", error=type(exc).__name__)
