# Revenue Recovery Voice Agent

Telephony-native AI receptionist for inbound home-service calls. Twilio Media
Streams carry native 8 kHz PCMU audio to OpenAI Realtime GA; the relay never
transcodes the audio. Calls, turns, tool outcomes, barge-ins, and post-call
analysis are persisted for dashboard replay.

## Quick start

```powershell
uv sync
Copy-Item .env.example .env
docker compose up -d postgres redis
uv run alembic upgrade head
uv run uvicorn apps.api.main:app --reload
```

The liveness endpoint is `GET /health`. `GET /health/ready` checks Postgres and
Redis. Configure a public HTTPS URL and set the Twilio voice webhook to
`POST /twiml/incoming` before making a real call.

## Services

- `apps/api`: FastAPI control plane, Twilio gateway, Realtime bridge, tools, and REST/SSE dashboard API.
- `apps/web`: Next.js dashboard at `http://localhost:3000`.
- `apps/eval`: offline scenario runner that exercises domain and tool contracts without media sockets.
- `worker`: Celery post-call recording, analysis, and CRM synchronization.
- Postgres uses pgvector for client knowledge-base retrieval; Redis is the Celery broker and readiness dependency.

## Commands

```powershell
uv run ruff check .
uv run mypy apps
uv run pytest
uv run kb-ingest northside-hvac --source .\knowledge
uv run eval-run
```

Tool failures return a typed envelope with a caller-safe `speak_hint`. Booking
is only confirmed after the calendar adapter returns success. Safety keywords
are matched deterministically before any other escalation or booking action.

## Configuration and safety

Client behavior lives in `config/clients/*.yaml`; the registry hot-reloads it
between calls. Secrets belong in `.env`, which is ignored by Git. Recording
storage requires captured consent, caller turns are PAN-redacted before they
are persisted, and outbound SMS checks the opt-out table before every send.

## Demo

The speakerphone narrative and failure beats are documented in
`docs/DEMO_SCRIPT.md`. Use the degraded Cal.com path and the safety-transfer
path as deliberate demonstrations, not hidden failure cases.
