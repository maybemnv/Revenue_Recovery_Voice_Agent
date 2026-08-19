# Revenue Recovery Voice Agent

Telephony-native AI receptionist for inbound home-service calls. Twilio Media
Streams carries native 8 kHz PCMU audio to OpenAI Realtime GA; the relay does
not transcode the audio. Calls, turns, tool outcomes, interruptions, and
post-call analysis are persisted for dashboard replay.

## Project status

This repository is a demo-oriented deployment, not a production template. The
deterministic tests and offline evaluation run locally, but a production/demo
sign-off still requires real provider calls, a clean database migration,
deployment health checks, and the live call beats described in
[`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

Use these documents as the source of truth:

- [`docs/deployment.md`](docs/deployment.md) — secrets, provider setup,
  infrastructure, go-live checks, and demo-day operations.
- [`docs/tasks.md`](docs/tasks.md) — remaining code-level work and verification
  tasks.
- [`task.md`](task.md) — original implementation plan and ordered evidence for
  the current demo hardening work.

## What is included

- `apps/api` — FastAPI control plane, Twilio webhooks and media gateway, OpenAI
  Realtime bridge, provider tools, REST/SSE dashboard API, and health checks.
- `apps/web` — Next.js dashboard for calls, transcripts, tool outcomes,
  interruptions, latency, cost, and escalation replay.
- `apps/eval` — offline scenario runner that exercises domain and tool
  contracts without requiring a live media socket or provider account.
- Celery worker — post-call recording, analysis, and CRM synchronization.
- PostgreSQL with pgvector — calls, turns, tool events, analysis, and knowledge
  base chunks. Redis provides readiness state and Celery queues.

## Prerequisites

Install the following before starting local development:

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 20 and npm for the dashboard
- Docker Desktop with Compose for PostgreSQL and Redis
- Provider accounts and credentials for a live demo: Twilio, OpenAI Realtime,
  Cal.com, HubSpot, and optionally Anthropic and Stripe

## Local development

1. Create the local environment file and fill in the required values:

   ```powershell
   Copy-Item .env.example .env
   ```

   At minimum, local startup needs valid database and Redis URLs. A real call
   additionally needs the provider keys and a public HTTPS tunnel. Never commit
   `.env` or paste its values into logs.

2. Install Python dependencies and start local infrastructure:

   ```powershell
   uv sync
   docker compose up -d postgres redis
   uv run alembic upgrade head
   ```

3. Start the API in one terminal:

   ```powershell
   uv run uvicorn apps.api.main:app --reload
   ```

4. Start the dashboard in a second terminal:

   ```powershell
   npm --prefix apps/web ci
   npm --prefix apps/web run dev
   ```

   The API is available at `http://localhost:8000` and the dashboard at
   `http://localhost:3000`.

5. Check service health:

   - `GET http://localhost:8000/health` — process liveness.
   - `GET http://localhost:8000/health/ready` — API, PostgreSQL, and Redis
     readiness.

## Docker Compose

### Provider-free fixture showcase

The fixture profile starts a deterministic Northside HVAC replay without Twilio,
OpenAI, Cal.com, HubSpot, or other paid-provider credentials. It binds the API
to `8101` and the dashboard to `3101`:

```powershell
docker compose --profile fixture up --build -d
Invoke-RestMethod http://localhost:8101/health
Invoke-RestMethod http://localhost:8101/health/ready
Invoke-RestMethod -Method Post http://localhost:8101/api/demo/reset-and-replay
Invoke-WebRequest http://localhost:3101
```

Open `http://localhost:3101/calls`, review the simulated call, then use Live
and Analytics. `/health/ready` reports fixture-data readiness separately from
process/dependency readiness. Reset only removes the named fixture record; it
does not touch other customer calls. Shut it down with:

```powershell
docker compose --profile fixture down
```

Fixture output is simulated replay data, not live-provider verification.

After creating `.env`, bring up the API, worker, dashboard, PostgreSQL, and
Redis together:

```powershell
docker compose up --build
```

The checked-in Compose file exposes PostgreSQL and Redis for local development
only. Do not expose those ports or reuse the development credentials in a
public staging or production deployment. Use private networking and managed
services there; follow [`docs/deployment.md`](docs/deployment.md).

The API image intentionally separates dependency caching from application
installation. It first installs locked third-party dependencies with
`--no-install-project`, then copies `apps/`, `README.md`, and the runtime
configuration before a second frozen sync installs the project itself. This
keeps the dependency layer cacheable while ensuring Hatchling has the complete
project metadata before the application starts.

## Provider and webhook setup

For a real call, set `PUBLIC_BASE_URL` to a stable public HTTPS origin. For
local work, start a tunnel such as `ngrok http 8000`, copy its HTTPS URL into
`.env`, and restart the API. Then configure the Twilio number:

- Voice webhook: `POST {PUBLIC_BASE_URL}/twiml/incoming`
- Status callback: `POST {PUBLIC_BASE_URL}/telephony/status`
- Media stream: generated by the API as a secure WebSocket URL under
  `{PUBLIC_BASE_URL}/media/{call_id}`

Set `TWILIO_VALIDATE_SIGNATURES=false` only for isolated local troubleshooting.
Keep it `true` in staging and production. Confirm the Twilio number, balance,
messaging sender, OpenAI Realtime access, Cal.com event type, and HubSpot token
before scheduling a live demo.

## Verification commands

Run the fast checks before opening or updating a PR:

```powershell
uv run ruff check .
uv run mypy apps
uv run pytest
uv run eval-run --json
uv lock --check
npm --prefix apps/web run build
uv run alembic heads
```

The offline evaluation is intentionally provider-free. Passing it does not
prove that Twilio, OpenAI, Cal.com, HubSpot, Redis, PostgreSQL, or the deployed
dashboard is reachable.

## Demo flow

The six-minute narrative is in [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md):

1. Place a real inbound call and show consent plus the greeting.
2. Qualify the HVAC request and show service-area lookup.
3. Interrupt the agent and show the barge-in/truncation markers.
4. Confirm a real calendar booking and SMS.
5. Degrade Cal.com and show the honest callback path.
6. Say “I smell gas” and show deterministic transfer to a human.
7. Replay the call in the dashboard.

Do not present a provider failure as a successful booking, read card digits
into the call, or present an automated outcome as a final human disposition.

## Configuration and safety

Client behavior lives in `config/clients/*.yaml`; the registry hot-reloads it
between calls. Recording requires captured consent. Caller turns and structured
tool arguments are redacted before persistence, and outbound SMS checks the
opt-out table before every send. Scope provider credentials to this demo and
rotate them if they appear in logs or other output.
