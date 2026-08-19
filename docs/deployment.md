 

# Deployment and Demo Readiness

## Fixture showcase deployment

For the provider-free sales fixture, Docker and Docker Compose are the only
prerequisites. From the repository root, run:

```powershell
docker compose --profile fixture up --build -d
Invoke-RestMethod http://localhost:8101/health
Invoke-RestMethod http://localhost:8101/health/ready
Invoke-RestMethod -Method Post http://localhost:8101/api/demo/reset-and-replay
Invoke-WebRequest http://localhost:3101
```

The API is fixed at `8101` and the web dashboard at `3101`; Postgres and Redis
remain Compose-internal dependencies. A ready response includes fixture-data
readiness without transcript or credential content. The reset route is enabled
only in fixture mode and clears only its deterministic labelled fixture call.
The expected browser result is a clearly simulated Northside call with booked,
degraded scheduling, escalation, live event, and analytics surfaces. End the
fixture stack with `docker compose --profile fixture down`. This path does not
verify live providers, recording, or any production deployment.

This is the operational checklist for the Revenue Recovery Voice Agent. It
covers provider accounts, environment configuration, infrastructure, live
verification, and demo-day execution. Code and test work belongs in
[`tasks.md`](tasks.md).

The current repository is a demo-oriented deployment, not a production
deployment template. The checked-in `docker-compose.yml` exposes local
Postgres and Redis ports and contains development credentials. Do not expose
that database or Redis setup to the public internet. Use private networking or
managed services for staging and production.

## Current release gate

The repository is currently code-green but not live-demo-ready. The latest
local evidence is recorded in [`task.md`](../task.md): 306 Python tests,
40/40 offline eval scenarios with zero critical failures, mypy, scoped Ruff,
the dashboard production build, lockfile validation, and the migration-head
check all pass. No provider call, clean database upgrade, `/health/ready`,
browser-width check, or live latency gate has been verified yet.

Use this document in order. Do not skip ahead to a live phone call until the
local stack and the smoke checks are green.

Demo sign-off requires all of the following:

- The D0 items in [`task.md`](../task.md) are complete and their evidence is
  recorded.
- A clean database migration, `/health/ready`, API, worker, and dashboard have
  been verified in the target environment.
- A real Twilio call completes the greeting, interruption, booking/degraded
  path, and safety-transfer beats in [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md).
- Dashboard viewer authentication works in the browser; the admin token is
  used only for configuration writes.
- Recording behavior, retention, and consent rules are approved for the
  jurisdictions where the service will be used.

Production readiness additionally requires the items tagged **[P]**, including
retention, backups, reconnect handling, alerting, and trace instrumentation.

## Fast path from this checkout

Run these steps from the repository root. They are deliberately split into a
Compose path and a host-process path because container DNS and host DNS use
different database/Redis URLs.

1. Create `.env` and fill every required value. Keep it untracked:

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

2. If the API and worker run in Docker Compose, use these service names in
   `.env`:

   ```dotenv
   DATABASE_URL=postgresql+asyncpg://voice:voice@postgres:5432/voice
   DATABASE_URL_SYNC=postgresql+psycopg://voice:voice@postgres:5432/voice
   REDIS_URL=redis://redis:6379/0
   CELERY_BROKER_URL=redis://redis:6379/1
   CELERY_RESULT_BACKEND=redis://redis:6379/2
   ```

   If the API is started directly on Windows while only Postgres and Redis
   run in Docker, use `localhost` instead. Do not mix the two forms.

3. Start Docker Desktop's Linux engine, then validate and start the stack:

   ```powershell
   docker info
   docker compose config
   docker compose up --build -d
   docker compose ps
   ```

4. Run the migration from the API container and verify both health endpoints:

   ```powershell
   docker compose exec api uv run alembic upgrade head
   Invoke-RestMethod http://localhost:8000/health/live
   Invoke-RestMethod http://localhost:8000/health/ready
   ```

   Readiness must report API, Postgres, and Redis as healthy. If it fails,
   inspect `docker compose logs api worker postgres redis` before touching the
   provider setup.

5. Open `http://localhost:3000`, authenticate as a viewer, and confirm the
   calls, live, and detail routes can reach the API. Use the admin token only
   when testing a configuration write.
6. Run the provider preflight in this order: OpenAI Realtime session, Twilio
   inbound webhook and media stream, Cal.com availability/booking, HubSpot
   contact/task sync, then Anthropic post-call analysis. The smoke call is the
   final check, not the first one.

## Environment variables and secrets

Create one environment set per environment. Never put real values in Git,
client YAML, Docker images, browser source, screenshots, or chat transcripts.
The API reads `.env` at the repository root; the API and worker use the same
settings. The dashboard calls a same-origin Next.js server proxy, which reads
`API_BASE_URL` and `DASHBOARD_VIEWER_TOKEN` on the server. Never prefix either
value with `NEXT_PUBLIC_`: dashboard tokens must not enter browser JavaScript.

| Variable                     |           Required | Kind                 | Production/demo value or rule                                                                                  |
| ---------------------------- | -----------------: | -------------------- | -------------------------------------------------------------------------------------------------------------- |
| `ENVIRONMENT`                |                yes | config               | `staging` or `production`; never leave `local` on a deployed service                                           |
| `LOG_LEVEL`                  |                yes | config               | `INFO` initially; use structured JSON logs in production                                                       |
| `PUBLIC_BASE_URL`            |                yes | routing              | Public HTTPS API origin, for example`https://voice.example.com`; must support the generated `wss://` media URL |
| `DATABASE_URL`               |                yes | secret-bearing DSN   | Async SQLAlchemy DSN, normally`postgresql+asyncpg://...`; private managed Postgres with pgvector               |
| `DATABASE_URL_SYNC`          |                yes | secret-bearing DSN   | Sync Psycopg DSN for Alembic and Celery, normally`postgresql+psycopg://...`                                    |
| `REDIS_URL`                  |                yes | secret-bearing DSN   | Readiness/live-state Redis database; use`rediss://` when TLS is required                                       |
| `CELERY_BROKER_URL`          |                yes | secret-bearing DSN   | Celery broker Redis database, separate from`REDIS_URL` where practical                                         |
| `CELERY_RESULT_BACKEND`      |                yes | secret-bearing DSN   | Celery result Redis database; set an expiry/retention policy                                                   |
| `OPENAI_API_KEY`             |                yes | secret               | Key with Realtime and embeddings access; scope and rotate it                                                   |
| `OPENAI_REALTIME_URL`        |                yes | endpoint             | Normally`wss://api.openai.com/v1/realtime`; verify against the account and model in use                        |
| `OPENAI_EMBEDDING_MODEL`     |                yes | config               | Must produce 1536-dimensional embeddings; the current schema expects`text-embedding-3-small`                   |
| `ANTHROPIC_API_KEY`          |            demo D1 | secret               | Key for post-call analysis; required if worker analysis is part of the demo                                    |
| `ANTHROPIC_ANALYSIS_MODEL`   |            demo D1 | config               | Confirm the model ID is available to the key before the demo                                                   |
| `TWILIO_ACCOUNT_SID`         |                yes | sensitive identifier | The Twilio account that owns the number and sends SMS/transfers                                                |
| `TWILIO_AUTH_TOKEN`          |                yes | secret               | Store only in the deployment secret manager; rotate if exposed                                                 |
| `TWILIO_MESSAGING_FROM`      |        yes for SMS | provider identifier  | E.164 Twilio number or approved messaging sender                                                               |
| `TWILIO_VALIDATE_SIGNATURES` |                yes | security config      | `true` in staging/production; `false` only for isolated local tunnel work                                      |
| `TWILIO_RECORDING_ENABLED`   |        demo opt-in | security/config      | `false` by default; set `true` only after the consent and jurisdiction review                                  |
| `CALCOM_API_KEY`             |            demo D0 | secret               | Key with availability, reservation, and booking access for the selected calendar                               |
| `CALCOM_API_BASE`            |                yes | endpoint             | Current default is`https://api.cal.com/v2`; verify the account's API version                                   |
| `CALCOM_EVENT_TYPE_ID`       |            demo D0 | config               | Numeric event type ID with open slots, correct duration, timezone, and team calendar                           |
| `HUBSPOT_ACCESS_TOKEN`       |            demo D1 | secret               | Private-app token with only the contact/call/task scopes required by the adapter                               |
| `HUBSPOT_API_BASE`           |                yes | endpoint             | Normally`https://api.hubapi.com`                                                                               |
| `STRIPE_API_KEY`             |       production/P | secret               | Use a test-mode key for the demo; use a live key only after payment/compliance sign-off                        |
| `STRIPE_PRICE_ID`            |       production/P | config               | Price object used by payment-link creation; leave the payment tool disabled if not configured                  |
| `SENTRY_DSN`                 |      production/D1 | sensitive endpoint   | DSN for the target environment; verify a forced exception arrives                                              |
| `LANGFUSE_PUBLIC_KEY`        |       production/P | identifier           | Project key for the approved Langfuse environment                                                              |
| `LANGFUSE_SECRET_KEY`        |       production/P | secret               | Server-side key only; never expose it to`apps/web`                                                             |
| `LANGFUSE_HOST`              |       production/P | endpoint             | Approved Langfuse host; setting this alone does not create traces until the code task is complete              |
| `CORS_ALLOW_ORIGINS`         |                yes | security config      | Comma-separated exact HTTPS dashboard origins; no wildcard in production                                       |
| `DASHBOARD_API_TOKEN`        |      yes for admin | secret               | Long random admin bearer token; required for config writes and admin API access                                |
| `DASHBOARD_VIEWER_TOKEN`     |     yes for viewer | secret               | Separate long random read-only bearer token; passed only to the Next.js server proxy                           |
| `API_BASE_URL`               | yes for web server | internal endpoint    | `http://api:8000` in Compose; `http://localhost:8000` when Next runs on the host                               |
| `NEXT_PUBLIC_API_BASE_URL`   |  optional fallback | public config        | Kept only as a proxy fallback for local compatibility; it is not used for browser API calls                    |
| `API_TOKEN`                  |         no / stale | unused example value | Not read by the current code. Do not rely on it; remove it from deployment secrets                             |
| `CLIENT_CONFIG_DIR`          |           optional | config               | Optional settings override; defaults to`config/clients` and is not listed in `.env.example`                    |

### Secret preflight

- [ ] Copy `.env.example` into the target secret store, not into a committed
      file. Replace every placeholder, `change-me`, localhost URL, and development
      credential.
- [ ] Generate separate random `DASHBOARD_API_TOKEN` and
      `DASHBOARD_VIEWER_TOKEN` values for local, staging, and production.
- [ ] Use separate provider projects/keys for staging and production where the
      provider supports it. Do not use a live Stripe key for the demo.
- [ ] Restrict provider scopes: Twilio account access, Cal.com calendar
      access, HubSpot private-app scopes, and Langfuse/Sentry project access.
- [ ] Confirm the secret manager does not print values in deployment logs and
      that crash/error reporting scrubs authorization headers and DSNs.
- [ ] Keep `TWILIO_RECORDING_ENABLED=false` until the approved consent notice,
      retention period, and jurisdiction decision are documented. Enabling it makes
      the API start a dual-track Twilio recording after the inbound webhook and
      persist only the completed callback URL.
- [ ] Confirm `.gitignore` excludes `.env` and `.env.*` while retaining only
      `.env.example`.
- [ ] Record the owner and rotation date for every provider credential in the
      team's password manager. Do not record secret values in this document.

## Client and provider setup

The environment does not contain the per-client behavior. Before deployment,
review [`config/clients/northside-hvac.yaml`](config/clients/northside-hvac.yaml):

- [ ] `phone_number` exactly matches the Twilio number's E.164 `To` value.
- [ ] `realtime.model`, stored `prompt_id`, `prompt_version`, and `voice` are
      reachable and approved for the account.
- [ ] `CALCOM_EVENT_TYPE_ID` or the YAML booking override points to the correct
      calendar, duration, timezone, and staff availability.
- [ ] `escalation.target_number` and `after_hours_target` point to a monitored
      human destination; do not leave the fictional demo number in a real client
      deployment.
- [ ] Service-area postcodes, hours, emergency rules, business name, pricing
      language, and brands are approved by the client.
- [ ] Only tools with working credentials and approved behavior are listed in
      `tools_enabled`. Keep spoken card capture out of the configuration.
- [ ] Load the client knowledge base with approved, current content. The repo
      currently contains the ingestion command but no committed `knowledge/`
      corpus; this is a deployment/data task, not a reason to invent answers.

## Infrastructure and deployment sequence

The target environment needs five independently observable pieces:

1. API/media process running `apps.api.main:app`.
2. Celery worker running `apps.api.workers.celery_app:app`.
3. Next.js web process built from `apps/web`.
4. PostgreSQL 16-compatible database with the `vector` extension.
5. Redis reachable by the API and worker.

Complete the sequence in this order:

- [ ] Provision private Postgres and Redis, with TLS, network rules, storage
      sizing, and monitoring. Do not use the development credentials from
      `docker-compose.yml`.
- [ ] Provision an HTTPS/WSS-capable API origin and reverse proxy. Preserve the
      original host and HTTPS scheme in forwarded headers because Twilio signature
      validation computes the public request URL.
- [ ] Configure the proxy for WebSocket upgrades on `/media/*`, no buffering
      for `/api/stream`, and idle timeouts longer than a live call. The API already
      emits `X-Accel-Buffering: no` for SSE.
- [ ] Inject the API/worker secrets before starting either process.
- [ ] Run `uv run alembic upgrade head` against a clean target database and
      verify the `vector` extension plus the seven application tables.
- [ ] Start the worker and verify it can consume a test task without leaving a
      retry storm or an unreviewed failure.
- [ ] Build the web image with `API_BASE_URL` set to the private deployed API
      origin and `DASHBOARD_VIEWER_TOKEN` injected only into the web server. Do not
      put server secrets in any `NEXT_PUBLIC_*` variable.
- [ ] Set `CORS_ALLOW_ORIGINS` to the deployed dashboard origin and verify
      `/health/live`, `/health/ready`, dashboard API access, and SSE from the real
      browser origin.
- [ ] Confirm `/health/ready` reports API, Postgres, and Redis as healthy before
      pointing Twilio at the service.

## Twilio and external callbacks

Configure the Twilio number and messaging service only after the public API is
healthy:

| Twilio setting                              | URL/method                                                 |
| ------------------------------------------- | ---------------------------------------------------------- |
| Inbound Voice webhook                       | `POST {PUBLIC_BASE_URL}/twiml/incoming`                    |
| Call status callback                        | `POST {PUBLIC_BASE_URL}/telephony/status`                  |
| Recording callback, if recording is enabled | `POST {PUBLIC_BASE_URL}/telephony/recording`               |
| Incoming SMS webhook for STOP handling      | `POST {PUBLIC_BASE_URL}/telephony/sms`                     |
| Generated bidirectional media stream        | `wss://.../media/{call_id}` from the returned TwiML        |
| Warm-transfer whisper URL                   | `{PUBLIC_BASE_URL}/telephony/whisper` generated by the API |

- [ ] Configure the number's status callback and, if recording is enabled, the
      recording callback at `/telephony/recording`. The application starts the
      recording through Twilio's live-call API only when
      `TWILIO_RECORDING_ENABLED=true`; it stores only a completed callback URL and
      serves playback through the authenticated dashboard API.
- [ ] If recording is enabled, verify the consent preamble is heard before the
      recording starts, the callback arrives as `completed`, and the viewer can
      play the audio from the call detail page. Keep it disabled until the legal
      review is complete.
- [ ] Send a signed callback from the real Twilio account and confirm invalid
      signatures receive `403` while valid callbacks are accepted.
- [ ] Confirm the Twilio number has voice and messaging capability, sufficient
      balance, and a tested transfer destination.
- [ ] Confirm the caller ID/sender is approved for the destination country and
      that STOP handling is enabled before any SMS test.

## Demo-ready operational checklist

The live narrative is in [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md). These
are the non-code tasks required to run it credibly.

### 48 hours before

- [ ] Complete and record a timed end-to-end rehearsal.
- [ ] Rehearse twice more: once with a clean interruption and once with a
      deliberate mumble/talk-over failure.
- [ ] Verify Cal.com has open slots on the demo date and the selected event type
      is in the correct timezone.
- [ ] Verify the Twilio balance, inbound routing, SMS sender, transfer target,
      and OpenAI/Anthropic/HubSpot/Cal.com credentials.
- [ ] Load the approved knowledge base and seed 8–10 realistic historical calls
      if the dashboard would otherwise be an empty state. Mark seeded data clearly.
- [ ] Confirm the dashboard auth flow with the actual viewer/admin roles.
- [ ] Charge the demo phone and test speakerphone acoustics in the actual room.
- [ ] Export the architecture diagram and record a three-minute fallback video.
- [ ] Prepare a clean browser profile, dashboard URL, login tokens, and a
      second person who can take over the phone if the primary device fails.

### Two hours before

- [ ] Deploy the exact release artifact; do not pull unreviewed working-tree
      code during the demo window.
- [ ] Run `uv run alembic upgrade head`/the platform migration job and verify
      the migration result.
- [ ] Confirm API, worker, database, Redis, and web services are up.
- [ ] Confirm `/health/ready` is green and review the first startup logs for
      disabled signatures, disabled dashboard auth, missing client configs, or
      missing optional-provider warnings.
- [ ] Confirm the stable tunnel/domain is up and the Twilio Voice webhook still
      points to it.
- [ ] Place one live smoke call from start to finish and verify its transcript,
      tool invocation, event trail, outcome, and post-call artifacts in the
      dashboard.
- [ ] Open the dashboard at 1280px, authenticated as viewer, on the call list.
- [ ] Have the Cal.com failure action ready, but use an approved reversible
      provider-failure procedure rather than killing production data/services.
- [ ] Keep the fallback video open in a background tab and keep the incident/
      rollback contact reachable.

### During the demo

- [ ] Beat 1: call the configured number and show the consent line and greeting.
- [ ] Beat 2: use the approved in-area address and show service-area handling.
- [ ] Beat 3: interrupt mid-sentence and show the barge-in/truncation evidence.
- [ ] Beat 4: confirm a real appointment only after Cal.com returns success; show
      the calendar and SMS confirmation.
- [ ] Beat 5: demonstrate provider degradation only in a safe staging/demo
      environment; the agent must promise a callback and must not claim a booking.
- [ ] Beat 6: say the approved safety phrase and show immediate transfer to a
      monitored human destination.
- [ ] Beat 7: show the transcript, tool chips, event trail, latency, cost, and
      post-call analysis without exposing raw secrets or unnecessary caller PII.

### Failure plan

- [ ] If the live call fails, switch to the prepared recording within 10
      seconds and describe it as a recorded fallback.
- [ ] If latency is visibly poor, state the measured result and the next action;
      do not imply that an unmeasured target passed.
- [ ] If an unscripted provider failure occurs, preserve the truthful degraded
      behavior and do not retry a booking manually while the caller is listening.
- [ ] If a safety transfer cannot reach a monitored human, stop the live demo;
      do not present voicemail or a failed transfer as a successful handoff.

## Production controls after the demo

- [ ] Define recording/transcript/trace retention periods, deletion ownership,
      and the jurisdiction-specific consent notice before accepting real client
      traffic.
- [ ] Configure encrypted Postgres backups and complete a restore test into a
      separate database. Record the restore time and row-count checks.
- [ ] Decide whether Redis is disposable ephemeral state or requires persistence;
      document the consequence of losing each Redis database.
- [ ] Configure alerts and an on-call owner for API/media exceptions, OpenAI
      session failures, provider failure spikes, worker backlog, and post-call SLA
      misses. The metric/trace implementation work is tracked in `tasks.md`.
- [ ] Write a rollback runbook for the app artifact, client YAML/prompt version,
      provider credentials, and database migrations. Test the app rollback against
      the current schema before go-live.
- [ ] Keep a kill-switch procedure for the Twilio Voice webhook and outbound SMS
      sender. Test that the team can disable traffic without deleting evidence.
- [ ] After the demo, rotate temporary demo credentials, remove seeded data if
      it contains synthetic PII-like values, and archive the run evidence.

## Useful smoke commands

```powershell
uv run ruff check .
uv run mypy apps
uv run pytest
uv run alembic upgrade head
uv run python -c "from apps.api.settings import get_settings; print(get_settings().environment)"
npm ci --prefix apps/web
npm run build --prefix apps/web
```

The commands prove local/build behavior only. They do not replace a real
Twilio call, provider booking, signed webhook, dashboard browser check, backup
restore, or latency measurement.
