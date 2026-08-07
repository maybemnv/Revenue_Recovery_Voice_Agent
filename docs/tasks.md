# Code-Level Tasks

This is the engineering backlog for the Revenue Recovery Voice Agent. It is
the code/test/configuration counterpart to [`deployment.md`](deployment.md),
which owns secrets, provider setup, infrastructure, rehearsals, and demo-day
operations.

The existing `task.md` is retained as the historical ordered plan and is not
overwritten because it contains working-tree edits. Use this file for the
code-level work that still gates a demo or production release. Do not check an
item off without the command, test output, or live evidence named in the item.

Priority language follows the historical plan: D0 blocks the demo, D1 improves
demo quality, and P is production hardening.

## Current working-tree items to validate

These changes are present locally but are not yet evidence of a completed
release:

- [ ] Review and land the current `apps/api/media/bridge.py` lifecycle changes:
  bounded outbound audio queue, barge-in queue discard, graceful drain,
  partial-turn flush, and idempotent socket close.
- [x] Focused media/realtime tests pass: `62 passed` across
  `tests/unit/test_bridge_passthrough.py` and
  `tests/unit/test_realtime_session.py`.
- [ ] Add `tests/unit/test_realtime_session.py` to the intended change set; it
  currently exists as an untracked GA handshake test even though it passes.
- [ ] Fix the five current Ruff findings in
  `tests/unit/test_media_lifecycle.py` (unused import/noqas, line length, and
  async timeout-parameter lint) before treating the release gate as green.
- [x] Full Python tests pass: `121 passed`; mypy passes across 61 source files.
  Ruff remains red until the item above is fixed.

## Demo-blocking engineering work

### Media plane and telephony

- [ ] Prove the real Twilio-to-OpenAI Realtime session with `audio/pcmu` in both
  directions and a real two-way audio call.
- [ ] Ensure the `stop` path closes both sockets, flushes the final partial
  turns, finalizes the call row, publishes the end event, and enqueues the
  post-call chain exactly once.
- [ ] Add integration coverage for Twilio `connected`, `start`, `media`,
  `mark`, and `stop` event ordering, including a disconnect during a response.
- [ ] Implement or explicitly remove recording playback as a product claim:
  the recording callback route exists, but current inbound TwiML does not
  request recording. If retained, add consent-safe TwiML/callback wiring and
  tests for the resulting lifecycle.
- [ ] Add a test proving invalid Twilio signatures are rejected for voice,
  status, recording, and SMS callbacks, with the public proxy URL represented
  correctly.
- [ ] Measure and gate answer latency, voice-to-voice p50/p95, barge-in cutoff,
  truncation accuracy, and booking dead air using real or provider-faithful
  integration fixtures.

### Dashboard access and demo surface

- [ ] Make the dashboard's authentication path work end to end. The API
  accepts `DASHBOARD_API_TOKEN`/`DASHBOARD_VIEWER_TOKEN`, but the current web
  client sends no `Authorization` header.
- [ ] Replace the unsafe browser-token approach with an intentional design for
  the deployed dashboard: server-side proxy/session, secure cookie, or another
  documented mechanism. `API_TOKEN` in `.env.example` is currently unused and
  must not be treated as authentication.
- [ ] Authenticate the browser SSE connection as well as REST calls; native
  `EventSource` cannot attach a bearer header without a supporting design.
- [ ] Complete the D0 dashboard styling integration and verify the live call
  list/detail flow at 1280px.
- [ ] Verify the call detail at 390px and ensure recording/transcript/event
  rendering does not leak unredacted caller data.
- [ ] Measure live SSE lag and verify the waveform, streaming transcript, and
  active-tool state against a real call.

### Booking and degraded behavior

- [ ] Confirm Cal.com reservation/hold/release behavior against a real account
  and exercise the fallback when the endpoint is unavailable.
- [ ] Measure dead air across ten booking calls and preserve the rule that
  fillers are out-of-band and only used for slow tools.
- [ ] Verify the configured Northside HVAC knowledge base is loaded and test
  five deliberately out-of-scope questions return `not_found` rather than an
  invented answer.
- [ ] Verify a real appointment, confirmation SMS, CRM task/contact, callback
  promise, and safety transfer end to end.
- [ ] Add a provider-faithful test that a failed booking can never reach the
  `booked` outcome or a success utterance.

## Reliability and production hardening

### Process and provider resilience

- [ ] Add graceful application shutdown that drains active media sessions before
  terminating the process, not only per-bridge close behavior.
- [ ] Add reconnect/close handling for Twilio and OpenAI socket drops mid-call,
  with a caller-safe outcome and persisted event.
- [ ] Mirror the playback ledger or equivalent live truncation state to Redis if
  a media worker restart must be survivable.
- [ ] Add bounded backpressure metrics and an explicit policy/test for dropped
  audio frames; verify the caller is not told the dropped response was heard.
- [ ] Add jittered retries and timeout budgets for Cal.com, HubSpot, Twilio
  REST, Stripe, OpenAI, and Anthropic, with no unsafe retry of a booking or SMS.
- [ ] Add a dead-letter queue/review workflow for exhausted Celery tasks and
  expose task age/backlog in operations metrics.

### Data safety and idempotency

- [ ] Prove no card-like digits reach persisted turns, logs, traces, or the
  post-call Anthropic request, including streaming/interrupted-turn paths.
- [ ] Apply the same redaction policy to logs and traces, not only database
  writes, and add regression tests for structured exception payloads.
- [ ] Make confirmation SMS idempotent on replay and test duplicate worker
  delivery does not send twice or bypass STOP suppression.
- [ ] Complete the 20-transcript analysis parse-rate test with 100% schema-valid
  output and no silent repair loop.
- [ ] Enforce a retention/deletion implementation for recordings, transcripts,
  and traces that matches the policy documented in `deployment.md`.
- [ ] Verify post-call artifacts appear in the dashboard within 90 seconds and
  emit an observable failure when the SLA is missed.

## Dashboard and configuration features

- [ ] Implement analytics for calls by hour, outcome distribution, recovered
  revenue estimate, and p50/p95 latency trend rather than leaving the current
  empty-state page as the production surface.
- [ ] Implement the admin-only client YAML editor using schema validation,
  atomic writes, and an audit record for each change.
- [ ] Prove client YAML hot reload takes effect on the next call without a
  media-plane redeploy and without corrupting an in-flight call's config.
- [ ] Add explicit error/loading/auth states for all dashboard pages and API
  failures; a 401 must not look like an empty database.

## Evals, observability, and release automation

- [ ] Add the Claude-as-judge rubric for tone and task completion while keeping
  hard safety/booking graders authoritative.
- [ ] Integrate Sentry for API/media and Celery failures and test it with a
  forced fault; confirm PII is not sent as default request data.
- [ ] Add Langfuse instrumentation linking the realtime session and post-call
  Claude chain under one trace ID, with redaction and retention controls.
- [ ] Emit/query metrics for voice-to-voice p50/p95, barge-in cutoff,
  truncation accuracy, tool latency/failure/retry, cost per call, worker
  backlog, and post-call SLA.
- [ ] Add automated alerts for media exceptions, OpenAI session failures, high
  p95 latency, provider failure spikes, worker backlog, and post-call SLA
  misses. Keep alert routing/ownership in `deployment.md`.
- [ ] Add CI for Ruff, mypy, pytest, web checks/build, and Docker image builds
  on every push and release candidate.
- [ ] Add a production deployment manifest or equivalent that does not expose
  the local Postgres/Redis ports or hardcode development credentials.
- [ ] Pin runtime/tool versions and make API, worker, and web image builds
  reproducible.

## Code-level release gates

- [ ] `uv run ruff check .` passes.
- [ ] `uv run mypy apps` passes.
- [ ] `uv run pytest` passes, including provider-contract and lifecycle tests.
- [ ] `uv run eval-run --json` meets at least 85% task success and zero
  critical safety failures.
- [ ] `npm ci --prefix apps/web` passes from a clean dependency directory.
- [x] `npm run build --prefix apps/web` passes on Next.js 15.5.22.
- [ ] A clean database upgrade creates the pgvector extension and all seven
  tables; migration rollback/forward behavior is documented.
- [ ] No code-level D0 blocker remains before the live demo; P items remain
  open only with an explicit production exception and owner.
