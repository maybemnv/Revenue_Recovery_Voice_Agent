"""The relay: Twilio Media Streams on one side, OpenAI Realtime on the other.

Two tasks run for the life of a call and neither blocks the other:

* `_pump_twilio` — inbound caller audio, forwarded base64-to-base64. The payload
  string Twilio sends is the payload string OpenAI receives. Both speak G.711
  μ-law at 8 kHz, so a transcode would be work done purely to undo itself, and
  every millisecond of it would land on the caller's ear.
* `_pump_openai` — outbound agent audio, plus every event that mutates call
  state: speech_started (barge-in), transcripts, function calls, errors.

Everything the agent sends to Twilio is followed by a `mark`. Twilio echoes the
mark only after playout, which is what gives `PlaybackLedger` a real measurement
instead of an assumption about what the caller heard.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import WebSocketDisconnect

from apps.api.config.schema import ClientConfig
from apps.api.domain.escalation import EscalationDecision, should_escalate
from apps.api.domain.state import CallState, ToolOutcome
from apps.api.media.barge_in import BargeInController
from apps.api.media.budget_guard import WRAP_UP_INSTRUCTION, BudgetGuard
from apps.api.media.playback_ledger import PlaybackLedger
from apps.api.media.realtime_client import (
    EV_ERROR,
    EV_FUNCTION_CALL_ARGS_DONE,
    EV_INPUT_TRANSCRIPT_COMPLETED,
    EV_OUTPUT_AUDIO_DELTA,
    EV_OUTPUT_AUDIO_TRANSCRIPT_DELTA,
    EV_OUTPUT_AUDIO_TRANSCRIPT_DONE,
    EV_OUTPUT_ITEM_ADDED,
    EV_RESPONSE_CANCELLED,
    EV_RESPONSE_DONE,
    EV_SPEECH_STARTED,
    EV_SPEECH_STOPPED,
    OOB_TOPIC_SENTIMENT,
    RealtimeClient,
    build_session_update,
    oob_topic,
    response_output_text,
)
from apps.api.observability.logging import bind_call_context, get_logger
from apps.api.security.redaction import redact_pan
from apps.api.tools.dispatch import FunctionCall, dispatch_with_masking
from apps.api.tools.registry import Invocation, RegistryBundle

log = get_logger(__name__)

# How often the budget ceiling is re-checked. Well under the wrap-up margin.
BUDGET_TICK_SECONDS = 2.0

# Bounded so a slow or stalled Twilio socket cannot grow memory without limit.
# Each entry is one audio delta; 200 of them is several seconds of speech, which
# is already far further behind than a healthy socket ever gets. Past that,
# dropping the oldest frame is strictly better than queueing until the process
# dies — the caller hears a glitch instead of the call ending.
OUTBOUND_QUEUE_MAX = 200

# How long a graceful close waits for queued audio to reach Twilio before
# giving up and closing anyway. A hung socket must not hold the call open.
DRAIN_TIMEOUT_SECONDS = 2.0

# The live sentiment classifier. Constrained to one word from a closed set so
# the reply is parseable without a schema, and phrased around what the *caller*
# expressed so it cannot be answered on the agent's behalf. `record_sentiment`
# treats anything outside the negative set as a streak reset, so an unparseable
# answer is a false negative — it never invents an escalation.
SENTIMENT_LABELS = ("positive", "neutral", "negative", "frustrated", "angry")
SENTIMENT_INSTRUCTIONS = (
    "Classify the emotional state the caller expressed in their most recent "
    "message. Reply with exactly one word from this list and nothing else: "
    + ", ".join(SENTIMENT_LABELS)
    + "."
)

# The classifier runs with `conversation: "none"`, so the model cannot see the
# conversation and the utterance has to travel in the instructions. Truncated
# because sentiment lives in the opening clause of a turn, and a caller who
# monologues should not cost a proportionally larger classification.
SENTIMENT_MAX_CHARS = 500


class TwilioSocket(Protocol):
    """The subset of a Starlette WebSocket the bridge uses."""

    async def send_json(self, payload: dict[str, Any]) -> None: ...
    async def receive_text(self) -> str: ...
    async def close(self, code: int = 1000) -> None: ...


EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]
TurnSink = Callable[[str, str, int, dict[str, Any]], Awaitable[None]]
TruncationSink = Callable[[str, int], Awaitable[None]]


@dataclass(slots=True)
class BridgeHooks:
    """Persistence and streaming, injected so the bridge itself stays testable."""

    on_event: EventSink | None = None
    on_turn: TurnSink | None = None
    on_truncation: TruncationSink | None = None
    on_invocation: Callable[[Invocation], Awaitable[None]] | None = None
    on_escalation: Callable[[EscalationDecision], Awaitable[None]] | None = None


@dataclass(slots=True)
class BridgeStats:
    frames_from_caller: int = 0
    frames_to_caller: int = 0
    frames_dropped: int = 0
    marks_sent: int = 0
    marks_acked: int = 0
    barge_ins: int = 0
    tool_calls: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class OutboundAudio:
    """One audio delta on its way to Twilio, with the mark that will follow it."""

    mark_name: str
    payload_b64: str
    decoded_bytes: int


class MediaBridge:
    def __init__(
        self,
        *,
        twilio: TwilioSocket,
        realtime: RealtimeClient,
        config: ClientConfig,
        state: CallState,
        tools: RegistryBundle,
        hooks: BridgeHooks | None = None,
    ) -> None:
        self.twilio = twilio
        self.realtime = realtime
        self.config = config
        self.state = state
        self.tools = tools
        self.hooks = hooks or BridgeHooks()

        self.ledger = PlaybackLedger()
        self.budget = BudgetGuard(config.budget)
        self.stats = BridgeStats()
        self.stream_sid: str = ""
        self.barge_in: BargeInController | None = None

        self._started = time.perf_counter()
        self._stop = asyncio.Event()
        self._mark_seq = 0
        self._pending_response_start: float | None = None
        self._first_audio_latency_ms: int | None = None
        self._outbound: asyncio.Queue[OutboundAudio] = asyncio.Queue(maxsize=OUTBOUND_QUEUE_MAX)
        self._partial_agent_text = ""
        self._closed = False

    # -- lifecycle --------------------------------------------------------
    async def run(self) -> BridgeStats:
        """Run until Twilio stops, the budget expires, or a socket dies."""
        tasks = [
            asyncio.create_task(self._pump_twilio(), name="pump_twilio"),
            asyncio.create_task(self._pump_openai(), name="pump_openai"),
            asyncio.create_task(self._pump_outbound(), name="pump_outbound"),
            asyncio.create_task(self._watch_budget(), name="watch_budget"),
        ]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            self._stop.set()
            # Let queued audio reach the caller before tearing the socket down.
            # A frame already handed to us was, as far as the model is concerned,
            # spoken; dropping it silently desyncs the transcript from what was
            # heard. Bounded, because a hung socket must not hold the call open.
            await self._drain_outbound()
            for task in pending:
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if (exc := task.exception()) is not None:
                    self.stats.errors.append(f"{task.get_name()}: {type(exc).__name__}")
                    log.warning("bridge_task_failed", task=task.get_name(), error=str(exc))
        finally:
            self._stop.set()
            await self.aclose()
        return self.stats

    async def stop(self) -> None:
        self._stop.set()

    async def shutdown(self) -> None:
        """Graceful process shutdown for one live call.

        Sets the stop flag, flushes whatever audio is still queued (bounded),
        then closes both sockets — which is what actually unblocks a pump that
        is waiting for a frame that will never come. `run()`'s own `finally`
        then runs the same teardown, and `aclose()` is idempotent, so calling
        this from a shutdown hook cannot double-close or double-flush.
        """
        self._stop.set()
        await self._drain_outbound()
        await self.aclose()

    async def aclose(self) -> None:
        """Flush the last partial turn, then close both sockets. Idempotent.

        Called from `run()`'s `finally` and again by graceful shutdown, so it has
        to tolerate being run twice — and it must never raise, because it runs on
        the path that finalises the call row.
        """
        if self._closed:
            return
        self._closed = True

        await self._flush_partial_turn()

        for name, closer in (("realtime", self.realtime.close), ("twilio", self.twilio.close)):
            try:
                await closer()
            except Exception as exc:  # a dead socket is the normal case here
                log.debug("socket_close_failed", socket=name, error=type(exc).__name__)

    async def _drain_outbound(self) -> None:
        try:
            await asyncio.wait_for(self._outbound.join(), timeout=DRAIN_TIMEOUT_SECONDS)
        except TimeoutError:
            log.warning("outbound_drain_timeout", queued=self._outbound.qsize())

    async def _flush_partial_turn(self) -> None:
        """Persist a turn the model was still speaking when the call ended.

        Without this a caller who hangs up mid-answer leaves a transcript that
        stops one turn early, and the dashboard shows a call that ended for no
        visible reason.
        """
        text = self._partial_agent_text.strip()
        self._partial_agent_text = ""
        if not text:
            return
        self.state.agent_turns += 1
        try:
            await self._record_turn("agent", text)
        except Exception:
            log.exception("final_turn_flush_failed")

    @property
    def elapsed_seconds(self) -> float:
        return time.perf_counter() - self._started

    # -- Twilio -> OpenAI -------------------------------------------------
    async def _pump_twilio(self) -> None:
        while not self._stop.is_set():
            try:
                raw = await self.twilio.receive_text()
            except WebSocketDisconnect:
                # Either the caller hung up without a `stop`, or a graceful
                # shutdown closed this socket underneath us. Both are ordinary
                # ends to a call, not errors to be recorded against it.
                log.info("twilio_socket_closed", stream_sid=self.stream_sid)
                self._stop.set()
                return
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            event = message.get("event")

            if event == "connected":
                await self._emit("twilio_connected", {})

            elif event == "media":
                # The one hot path. base64 in, same base64 out, no decode.
                self.stats.frames_from_caller += 1
                await self.realtime.append_audio(message["media"]["payload"])

            elif event == "start":
                await self._on_stream_start(message)

            elif event == "mark":
                name = message.get("mark", {}).get("name", "")
                if self.ledger.on_mark_ack(name):
                    self.stats.marks_acked += 1

            elif event == "stop":
                log.info("twilio_stream_stopped", stream_sid=self.stream_sid)
                self._stop.set()
                return

    async def _on_stream_start(self, message: dict[str, Any]) -> None:
        start = message.get("start", {})
        self.stream_sid = start.get("streamSid", "")
        bind_call_context(stream_sid=self.stream_sid)
        self.barge_in = BargeInController(
            twilio=self.twilio,
            realtime=self.realtime,
            ledger=self.ledger,
            stream_sid=self.stream_sid,
            emit_event=self.hooks.on_event,
        )
        await self.realtime.send_json(
            build_session_update(self.config, self.tools.registry.openai_tools())
        )
        if self.config.realtime.greeting:
            # Greet without waiting to be spoken to. `input: []` keeps the fixed
            # greeting out of conversation state exactly like a filler.
            await self.realtime.say_out_of_band(self.config.realtime.greeting)
        else:
            await self.realtime.create_response()
        self._pending_response_start = time.perf_counter()
        await self._emit("stream_started", {"stream_sid": self.stream_sid})

    # -- OpenAI -> Twilio -------------------------------------------------
    async def _pump_openai(self) -> None:
        async for event in self.realtime.events():
            if self._stop.is_set():
                return
            kind = event.get("type", "")

            if kind == EV_OUTPUT_AUDIO_DELTA:
                await self._send_audio(event.get("delta", ""))

            elif kind == EV_SPEECH_STARTED:
                await self._on_barge_in()

            elif kind == EV_SPEECH_STOPPED:
                # Start the user-visible latency clock. It ends at the first
                # outbound audio byte, not when the model finishes a response.
                self._pending_response_start = time.perf_counter()
                self._first_audio_latency_ms = None

            elif kind == EV_OUTPUT_ITEM_ADDED:
                item = event.get("item", {})
                if item.get("type") == "message":
                    self.ledger.on_item_started(item.get("id", ""))

            elif kind == EV_INPUT_TRANSCRIPT_COMPLETED:
                await self._on_caller_transcript(event.get("transcript", "") or "")

            elif kind == EV_OUTPUT_AUDIO_TRANSCRIPT_DELTA:
                # Accumulated only so a call that ends mid-answer still persists
                # what the agent had said. The `.done` event below is the normal
                # path and clears this.
                self._partial_agent_text += event.get("delta", "") or ""

            elif kind == EV_OUTPUT_AUDIO_TRANSCRIPT_DONE:
                self._partial_agent_text = ""
                await self._on_agent_transcript(event.get("transcript", "") or "")

            elif kind == EV_FUNCTION_CALL_ARGS_DONE:
                await self._on_function_call(event)

            elif kind == EV_RESPONSE_DONE:
                # An out-of-band response finishing is not the spoken turn
                # finishing. It runs concurrently with one, so resetting the
                # latency clock here would discard the pending voice-to-voice
                # measurement and attribute the wrong number to the next turn.
                if oob_topic(event) == OOB_TOPIC_SENTIMENT:
                    await self._on_sentiment_verdict(response_output_text(event))
                else:
                    self._pending_response_start = None
                    self._first_audio_latency_ms = None

            elif kind == EV_RESPONSE_CANCELLED:
                self._pending_response_start = None

            elif kind == EV_ERROR:
                detail = event.get("error", {})
                self.stats.errors.append(str(detail.get("code", "unknown")))
                log.warning("realtime_error", error=detail)

    async def _send_audio(self, delta_b64: str) -> None:
        """Queue one audio delta. The outbound pump does the actual writing.

        Enqueueing rather than writing inline is what makes the queue bounded:
        a Twilio socket that stops draining backs up here, where the depth is
        visible and capped, instead of inside an unbounded transport buffer.
        """
        if not delta_b64 or not self.stream_sid:
            return
        self._mark_seq += 1
        item = OutboundAudio(
            mark_name=f"m{self._mark_seq}",
            payload_b64=delta_b64,
            # Byte length of the decoded μ-law, which is what the ledger counts in.
            decoded_bytes=_b64_decoded_size(delta_b64),
        )
        try:
            self._outbound.put_nowait(item)
        except asyncio.QueueFull:
            # The socket is not keeping up. Drop the oldest frame rather than
            # the newest: stale audio is the least useful thing in the queue,
            # and unbounded growth is the failure this ceiling exists to prevent.
            with contextlib.suppress(asyncio.QueueEmpty):
                self._outbound.get_nowait()
                self._outbound.task_done()
            self.stats.frames_dropped += 1
            log.warning("outbound_queue_full", dropped=self.stats.frames_dropped)
            with contextlib.suppress(asyncio.QueueFull):
                self._outbound.put_nowait(item)

    async def _pump_outbound(self) -> None:
        """Write queued audio to Twilio, marking and ledgering each frame.

        `on_chunk_sent` is called here, at the point of the actual write, not at
        enqueue time. A frame dropped by a barge-in never reaches this method, so
        it never enters the ledger — which is what keeps `audio_end_ms` a
        statement about what the caller heard rather than what we intended.
        """
        while True:
            item = await self._outbound.get()
            try:
                await self.twilio.send_json(
                    {
                        "event": "media",
                        "streamSid": self.stream_sid,
                        "media": {"payload": item.payload_b64},
                    }
                )
                await self.twilio.send_json(
                    {
                        "event": "mark",
                        "streamSid": self.stream_sid,
                        "mark": {"name": item.mark_name},
                    }
                )
                self.ledger.on_chunk_sent(item.mark_name, item.decoded_bytes)
                if (
                    self._pending_response_start is not None
                    and self._first_audio_latency_ms is None
                ):
                    self._first_audio_latency_ms = int(
                        (time.perf_counter() - self._pending_response_start) * 1000
                    )
                self.stats.frames_to_caller += 1
                self.stats.marks_sent += 1
            finally:
                self._outbound.task_done()

    def _discard_queued_audio(self) -> int:
        """Drop everything not yet written. Returns how many frames went.

        Called before Twilio's `clear` on a barge-in: our own queue has to go
        first, or the pump keeps writing frames after the flush and the caller
        hears the agent talking over them anyway.
        """
        dropped = 0
        while True:
            try:
                self._outbound.get_nowait()
            except asyncio.QueueEmpty:
                return dropped
            self._outbound.task_done()
            dropped += 1

    # -- state transitions ------------------------------------------------
    async def _on_barge_in(self) -> None:
        if self.barge_in is None:
            return
        # Local queue first, then Twilio's buffer. Reversing these means the pump
        # writes fresh audio in behind the `clear` that was meant to silence it.
        discarded = self._discard_queued_audio()
        if discarded:
            log.debug("barge_in_discarded_queued_audio", frames=discarded)
        result = await self.barge_in.on_speech_started()
        if result.truncated:
            self.stats.barge_ins += 1
        if result.item_id and self.hooks.on_truncation is not None:
            await self.hooks.on_truncation(result.item_id, result.audio_end_ms)

    async def _on_caller_transcript(self, text: str) -> None:
        if not text.strip():
            return
        self.state.last_caller_text = text
        self.state.caller_turns += 1
        await self._record_turn("caller", text)
        await self._check_escalation()
        await self._classify_sentiment(text)

    async def _classify_sentiment(self, text: str) -> None:
        """Ask for a sentiment label out-of-band. Advisory, so failure is silent.

        Fired after the turn is recorded and escalation has been evaluated, so a
        classifier that never answers cannot delay either. The verdict lands on a
        later `response.done` and feeds the *next* escalation check — sentiment
        escalation needs two consecutive negative turns anyway, so nothing is
        lost by the label arriving one turn behind.
        """
        if not self.config.escalation.live_sentiment or self.state.escalated:
            return
        utterance = redact_pan(text.strip())[:SENTIMENT_MAX_CHARS]
        try:
            await self.realtime.classify_out_of_band(
                f'{SENTIMENT_INSTRUCTIONS}\n\nCaller said: "{utterance}"',
                OOB_TOPIC_SENTIMENT,
            )
        except Exception as exc:
            # Never break a live call over a classification the call can run
            # without. A socket that is genuinely gone surfaces in the pumps.
            log.debug("sentiment_request_failed", error=type(exc).__name__)

    async def _on_sentiment_verdict(self, label: str) -> None:
        normalised = label.strip().strip(".").lower()
        if normalised not in SENTIMENT_LABELS:
            log.debug("sentiment_unparsed", raw=normalised[:40])
            return
        self.state.record_sentiment(normalised)
        await self._emit(
            "sentiment",
            {
                "label": normalised,
                "negative_turns": self.state.negative_sentiment_turns,
            },
        )
        await self._check_escalation()

    async def _on_agent_transcript(self, text: str) -> None:
        if not text.strip():
            return
        self.state.agent_turns += 1
        await self._record_turn("agent", text)

    async def _record_turn(self, role: str, text: str) -> None:
        if self.hooks.on_turn is None:
            return
        at_ms = int(self.elapsed_seconds * 1000)
        meta: dict[str, Any] = {"item_id": self.ledger.current_item_id}
        if role == "agent":
            latency_ms = self._first_audio_latency_ms
            if latency_ms is None and self._pending_response_start is not None:
                latency_ms = int((time.perf_counter() - self._pending_response_start) * 1000)
            if latency_ms is not None:
                meta["latency_ms"] = latency_ms
        await self.hooks.on_turn(role, text, at_ms, meta)

    async def _check_escalation(self) -> None:
        if self.state.escalated:
            return
        decision = should_escalate(self.state, self.config)
        if decision is None:
            return

        self.state.escalated = True
        log.info("escalation_triggered", reason=str(decision.reason), detail=decision.detail)
        await self._emit(
            "escalation",
            {"reason": str(decision.reason), "detail": decision.detail,
             "immediate": decision.is_immediate},
        )
        if self.hooks.on_escalation is not None:
            await self.hooks.on_escalation(decision)

        if decision.is_immediate and self.barge_in is not None:
            # Safety pre-empts whatever is playing, including a booking readback.
            await self.barge_in.on_speech_started()

        transfer = self.tools.registry.get("transfer_to_human")
        if transfer is None:
            return
        call = FunctionCall(
            name="transfer_to_human",
            call_id=f"escalation-{uuid.uuid4().hex[:8]}",
            arguments={"reason": decision.detail},
        )
        await self._dispatch(call)

    async def _on_function_call(self, event: dict[str, Any]) -> None:
        await self._dispatch(FunctionCall.from_event(event))

    async def _dispatch(self, call: FunctionCall) -> None:
        self.stats.tool_calls += 1
        invocation = await dispatch_with_masking(
            call,
            registry=self.tools.registry,
            realtime=self.realtime,
            context=self.tools.context,
            on_invocation=self.hooks.on_invocation,
        )
        self._pending_response_start = time.perf_counter()
        self.state.record_tool(
            ToolOutcome(
                name=invocation.name,
                status=invocation.result["status"],
                latency_ms=invocation.latency_ms,
                attempt=invocation.attempts,
            )
        )
        await self._emit(
            "tool_call",
            {
                "tool": invocation.name,
                "status": invocation.result["status"],
                "latency_ms": invocation.latency_ms,
                "filler_played": invocation.filler_played,
            },
        )
        # A tool-failure streak is itself an escalation trigger.
        await self._check_escalation()

    # -- budget -----------------------------------------------------------
    async def _watch_budget(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(BUDGET_TICK_SECONDS)
            status = self.budget.check(self.elapsed_seconds)

            if status.should_end:
                log.info(
                    "budget_expired",
                    binding=status.binding,
                    elapsed_seconds=round(status.elapsed_seconds, 1),
                    cost_usd=status.estimated_cost_usd,
                )
                await self._emit("budget_expired", {"binding": status.binding})
                self._stop.set()
                return

            if self.budget.needs_wrap_up_now(self.elapsed_seconds) is not None:
                self.budget.mark_wrap_up_sent()
                log.info("budget_wrap_up", binding=status.binding, pct=status.seconds_pct)
                await self.realtime.send_json(
                    {
                        "type": "session.update",
                        "session": {"type": "realtime", "instructions": WRAP_UP_INSTRUCTION},
                    }
                )
                await self._emit("budget_wrap_up", {"binding": status.binding})

    async def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.hooks.on_event is not None:
            await self.hooks.on_event(kind, payload)


def _b64_decoded_size(payload: str) -> int:
    """Decoded byte count without allocating the decode.

    Every 4 base64 chars carry 3 bytes, minus one per '=' of padding.
    """
    if not payload:
        return 0
    padding = payload.count("=", -2)
    return (len(payload) // 4) * 3 - padding


def decode_size_exact(payload: str) -> int:
    """Reference implementation the passthrough test asserts `_b64_decoded_size` against."""
    return len(base64.b64decode(payload))
