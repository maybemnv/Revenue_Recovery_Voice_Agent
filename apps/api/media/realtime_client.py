"""OpenAI Realtime **GA** client.

Two things here are the difference between working and not, and both are places
public tutorials are still wrong:

* **No `OpenAI-Beta: realtime=v1` header.** GA rejects the beta payload shapes,
  and sending the header opts you into them.
* **Nested audio config.** `session.audio.input.format` / `session.audio.output.format`,
  not flat `input_audio_format` / `output_audio_format`. See `docs/PRD.md:300-327`.

Event names are GA too: `response.output_audio.delta`, not `response.audio.delta`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from apps.api.config.schema import ClientConfig
from apps.api.observability.logging import get_logger
from apps.api.settings import get_settings

log = get_logger(__name__)

# Twilio's native wire format. Identical on both sides so the relay never
# decodes, resamples, or re-encodes a single frame.
PCMU = {"type": "audio/pcmu"}

# --- GA event names, referenced by the bridge -------------------------------
EV_SESSION_UPDATED = "session.updated"
EV_OUTPUT_AUDIO_DELTA = "response.output_audio.delta"
EV_OUTPUT_AUDIO_DONE = "response.output_audio.done"
EV_OUTPUT_AUDIO_TRANSCRIPT_DELTA = "response.output_audio_transcript.delta"
EV_OUTPUT_AUDIO_TRANSCRIPT_DONE = "response.output_audio_transcript.done"
EV_SPEECH_STARTED = "input_audio_buffer.speech_started"
EV_SPEECH_STOPPED = "input_audio_buffer.speech_stopped"
EV_INPUT_TRANSCRIPT_COMPLETED = "conversation.item.input_audio_transcription.completed"
EV_RESPONSE_CREATED = "response.created"
EV_RESPONSE_DONE = "response.done"
EV_RESPONSE_CANCELLED = "response.cancelled"
EV_OUTPUT_ITEM_ADDED = "response.output_item.added"
EV_FUNCTION_CALL_ARGS_DONE = "response.function_call_arguments.done"
EV_ERROR = "error"


class RealtimeError(RuntimeError):
    pass


def build_session_update(cfg: ClientConfig, tools: list[dict[str, Any]]) -> dict[str, Any]:
    """The GA `session.update` payload for one client.

    Kept as a pure function so a test can assert the shape without a socket.
    """
    rt = cfg.realtime
    session: dict[str, Any] = {
        "type": "realtime",
        "model": rt.model,
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "format": PCMU,
                "turn_detection": {
                    "type": rt.turn_detection.type,
                    "interrupt_response": rt.turn_detection.interrupt_response,
                },
                "transcription": {"model": rt.transcription_model},
            },
            "output": {"format": PCMU, "voice": rt.voice},
        },
        "tool_choice": "auto",
    }
    if tools:
        session["tools"] = tools

    # A server-stored prompt is preferred: per-client persona tuning happens in
    # the OpenAI prompt editor and a bad prompt is a one-integer rollback.
    # `schema.py` already guarantees one of the two is present.
    if rt.prompt_id:
        prompt: dict[str, Any] = {"id": rt.prompt_id}
        if rt.prompt_version:
            prompt["version"] = rt.prompt_version
        if rt.variables:
            prompt["variables"] = dict(rt.variables)
        session["prompt"] = prompt
    else:
        session["instructions"] = rt.instructions

    return {"type": "session.update", "session": session}


class RealtimeClient:
    """One WSS connection to OpenAI Realtime, for the lifetime of one call."""

    def __init__(self, *, model: str, api_key: str | None = None, url: str | None = None) -> None:
        settings = get_settings()
        self._model = model
        self._api_key = api_key if api_key is not None else settings.openai_api_key
        self._url = f"{(url or settings.openai_realtime_url).rstrip('/')}?model={model}"
        self._ws: ClientConnection | None = None
        self._send_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def connect(self) -> None:
        # Deliberately no OpenAI-Beta header. GA only.
        headers = {"Authorization": f"Bearer {self._api_key}"}
        self._ws = await websockets.connect(
            self._url,
            additional_headers=headers,
            max_size=None,  # audio deltas exceed the default frame ceiling
            ping_interval=20,
            ping_timeout=20,
        )
        log.info("realtime_connected", model=self._model)

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise RealtimeError("realtime socket is not connected")
        # One writer at a time: the bridge, the tool dispatcher, and the barge-in
        # controller all send on this socket from different tasks.
        async with self._send_lock:
            await self._ws.send(json.dumps(payload))

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        if self._ws is None:
            raise RealtimeError("realtime socket is not connected")
        async for raw in self._ws:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                log.warning("realtime_bad_frame", size=len(raw))

    # -- convenience senders ---------------------------------------------
    async def append_audio(self, base64_payload: str) -> None:
        """Forward Twilio's base64 μ-law byte-identical. No transcode."""
        await self.send_json(
            {"type": "input_audio_buffer.append", "audio": base64_payload}
        )

    async def create_response(self, response: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"type": "response.create"}
        if response is not None:
            payload["response"] = response
        await self.send_json(payload)

    async def say_out_of_band(self, line: str) -> None:
        """Speak a fixed line without it entering conversation state.

        `input: []` is the GA primitive that makes latency-masking fillers and the
        sentiment classifier possible: the model speaks, but the turn is not part
        of the conversation the next response reasons over.
        """
        await self.create_response(
            {"input": [], "instructions": f'Say exactly: "{line}"'}
        )

    async def classify_out_of_band(self, instructions: str, topic: str) -> None:
        """Text-only, conversation-less response. Emits no audio."""
        await self.create_response(
            {
                "conversation": "none",
                "input": [],
                "output_modalities": ["text"],
                "instructions": instructions,
                "metadata": {"topic": topic},
            }
        )

    async def send_function_output(self, call_id: str, output: str) -> None:
        await self.send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            }
        )

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
            log.info("realtime_closed")

    async def __aenter__(self) -> RealtimeClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
